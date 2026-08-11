"""Task management module."""
from flask import jsonify
from firebase_admin import firestore
import traceback
from datetime import datetime, timezone
from modules.config import IRAQ_TIMEZONE

# ---------------------------------------------------------------------------
# Reconcile helpers
# ---------------------------------------------------------------------------

def _extract_influencer_priority_map(client):
    """Return a mapping of doctor name → priority string for all influencer
    doctors on the client that have a non-empty name.

    Args:
        client: Client dict (includes 'additionalInfo.doctors[]').

    Returns:
        dict[str, str]  e.g. {"Dr. X": "A", "Dr. Y": "C"}
    """
    result = {}
    additional_info = client.get("additionalInfo") or {}
    for doctor in additional_info.get("doctors", []):
        if not doctor.get("isInfluencer", False):
            continue
        name = doctor.get("name", "").strip()
        if not name:
            continue
        priority = doctor.get("priority")
        if isinstance(priority, dict):
            priority = priority.get("name") or priority.get("value") or "C"
        result[name] = priority or "C"
    return result


def _is_task_completed(task):
    """Return True when a task's status represents a completed visit.

    Matches both the wire value ('completed') and the Arabic display name.
    """
    return task.get("status") in ("completed", "مكتمل")


def _task_is_protected(task):
    """Return True when a task holds real fieldwork and must never be deleted.

    A task is protected when it is completed, or when a sales rep already
    recorded a visit result on it (whatever its current status).
    """
    return _is_task_completed(task) or task.get("visitResult") is not None


def _marketing_task_name(marketing_task):
    """Normalise a marketing task (string or dict) to its comparable name."""
    if isinstance(marketing_task, dict):
        return (
            marketing_task.get("name")
            or marketing_task.get("id")
            or str(marketing_task)
        )
    return str(marketing_task)


def _task_identity_key(plan_id, client_id, product_id, marketing_task_name, doctor_name):
    """Return the tuple that uniquely identifies a planned task.

    Mirrors the duplicate-check query in `_create_doctor_task` so callers can
    dedupe in memory instead of issuing one Firestore query per combination.
    """
    return (
        plan_id,
        client_id,
        product_id,
        marketing_task_name,
        doctor_name or "",
    )


def _doctor_priority_name(doctor):
    """Return the A/B/C priority for an influencer doctor.

    Tasks are generated per influencer doctor, so the task priority must come
    from that doctor — not from the client (which no longer carries a priority).
    Handles enum name/value dicts and falls back to 'C'.
    """
    priority = doctor.get("priority")
    if isinstance(priority, dict):
        return priority.get("name") or priority.get("value") or "C"
    if isinstance(priority, str) and priority:
        return priority
    return "C"


def _build_planned_task_payload(plan_id, client_id, product_id, marketing_task_name, doctor):
    """Build a planned task document matching the Flutter TaskModel structure."""
    return {
        "taskType": "planned",  # TaskType.planned.value
        "assignedToId": None,
        "planId": plan_id,
        "clientId": client_id,
        "targetDate": None,  # Optional, can be set later
        "productId": product_id,
        "status": "pending",  # Default status (TaskStatus enum)
        "cancelReason": None,  # Optional
        "reviewState": "approved",  # Default review state (ReviewState enum)
        "visitResult": None,  # Optional
        "priority": _doctor_priority_name(doctor),
        "note": None,  # Optional
        "doctorName": doctor.get("name", ""),  # Doctor name from influencer doctor
        "createdAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
        "updatedAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
        "marketingTask": marketing_task_name,  # Stored as string to match duplicate check query
    }


def _fetch_eligible_clients(department_ids, cities, db):
    """Fetch eligible clients based on departments and cities.
    
    Handles Firebase whereIn limitation (max 10 values) by batching.
    Returns detailed error information if no clients found.

    Args:
        department_ids: List of department IDs
        cities: List of city names
        db: Firestore database instance

    Returns:
        List of client dictionaries with id added
        
    Raises:
        Exception: If no clients found or query fails, with detailed error info
    """
    department_ids_list = list(department_ids) if isinstance(department_ids, set) else department_ids
    cities_list = list(cities) if isinstance(cities, set) else cities
    
    # Validation
    if not department_ids_list:
        raise Exception("No department IDs provided")
    if not cities_list:
        raise Exception("No cities provided")
    
    all_clients = []
    batch_size = 10
    query_errors = []
    diagnostic_info = {
        "total_clients_in_db": 0,
        "sample_states": [],
        "sample_departments": [],
        "sample_cities": [],
        "department_matches": 0,
        "city_matches": 0,
        "combined_matches": 0,
        "dept_mismatches": [],
        "city_mismatches": []
    }
    
    try:
        # Analyze database structure for diagnostics
        sample_clients = list(db.collection("clients").limit(50).stream())
        diagnostic_info["total_clients_in_db"] = len(sample_clients)
        
        if diagnostic_info["total_clients_in_db"] == 0:
            raise Exception("Database is empty - no clients found in database")
        
        # Collect sample data
        sample_states = set()
        sample_departments = set()
        sample_cities = set()
        
        for doc in sample_clients:
            client_data = doc.to_dict()
            state = client_data.get('state')
            if state:
                sample_states.add(str(state))
            
            dept = client_data.get('department')
            if dept:
                if isinstance(dept, dict):
                    dept_id = dept.get('id') or dept.get('_id')
                    if dept_id:
                        sample_departments.add(str(dept_id))
                else:
                    sample_departments.add(str(dept))
            
            city = client_data.get('city')
            if city:
                sample_cities.add(str(city).strip())
        
        # Get all unique cities from database (for better matching)
        # This helps identify if cities exist but weren't in the sample
        all_db_cities = set()
        try:
            for doc in db.collection("clients").select(["city"]).stream():
                city = doc.to_dict().get('city')
                if city:
                    all_db_cities.add(str(city).strip())
        except Exception:
            # If select fails, fall back to sample
            all_db_cities = sample_cities
        
        diagnostic_info["sample_states"] = list(sample_states)
        diagnostic_info["sample_departments"] = list(sample_departments)[:20]
        diagnostic_info["sample_cities"] = list(sample_cities)[:20]
        diagnostic_info["all_db_cities"] = list(all_db_cities)[:50]  # All cities found in DB
        
        # Test individual queries for diagnostics
        for dept_id in department_ids_list[:5]:
            try:
                dept_results = list(
                    db.collection("clients")
                    .where("department", "==", dept_id)
                    .limit(3)
                    .stream()
                )
                if dept_results:
                    diagnostic_info["department_matches"] += len(dept_results)
            except Exception:
                pass

        for city_name in cities_list[:5]:
            try:
                city_results = list(
                    db.collection("clients")
                    .where("city", "==", city_name)
                    .limit(3)
                    .stream()
                )
                if city_results:
                    diagnostic_info["city_matches"] += len(city_results)
            except Exception:
                pass

        if department_ids_list and cities_list:
            try:
                combined_results = list(
                    db.collection("clients")
                    .where("department", "==", department_ids_list[0])
                    .where("city", "==", cities_list[0])
                    .limit(3)
                    .stream()
                )
                diagnostic_info["combined_matches"] = len(combined_results)
            except Exception:
                pass
        
        # Check for mismatches
        for dept_id in department_ids_list:
            if dept_id not in sample_departments:
                diagnostic_info["dept_mismatches"].append(dept_id)
        
        # Check cities against all database cities (normalized)
        for city_name in cities_list:
            city_normalized = str(city_name).strip()
            if city_normalized not in all_db_cities:
                diagnostic_info["city_mismatches"].append(city_name)
        
        # Normalize city names for matching (trim whitespace)
        cities_set = {str(city).strip() for city in cities_list}
        
        # Approved state to filter by
        
        # Execute batched queries
        # Strategy: If we have many cities (>10), query by department first, then filter by city in memory
        # Otherwise, use the standard batching approach
        if len(cities_list) > 10:
            # Query by department batches, then filter by city and state in memory
            for i in range(0, len(department_ids_list), batch_size):
                batch_departments = department_ids_list[i:i + batch_size]
                
                try:
                    base_query = db.collection("clients")
                    
                    if len(batch_departments) == 1:
                        base_query = base_query.where("department", "==", batch_departments[0])
                    else:
                        base_query = base_query.where("department", "in", batch_departments)
                    
                    # Get all clients matching departments, then filter by city
                    for doc in base_query.stream():
                        client = doc.to_dict()
                        client_city = str(client.get("city", "")).strip()

                        # Check if client's city matches any requested city
                        if client_city in cities_set:
                            client["id"] = doc.id
                            all_clients.append(client)
                            
                except Exception as query_error:
                    query_errors.append({
                        "departments": batch_departments,
                        "cities": "all (filtered in memory)",
                        "error": str(query_error)
                    })
                    continue
        else:
            # Standard batching: both department and city, with state filter
            for i in range(0, len(department_ids_list), batch_size):
                batch_departments = department_ids_list[i:i + batch_size]
                
                for j in range(0, len(cities_list), batch_size):
                    batch_cities = cities_list[j:j + batch_size]
                    
                    try:
                        base_query = db.collection("clients")
                        
                        if len(batch_departments) == 1:
                            base_query = base_query.where("department", "==", batch_departments[0])
                        else:
                            base_query = base_query.where("department", "in", batch_departments)
                        
                        if len(batch_cities) == 1:
                            base_query = base_query.where("city", "==", batch_cities[0])
                        else:
                            base_query = base_query.where("city", "in", batch_cities)
                        
                        for doc in base_query.stream():
                            client = doc.to_dict()
                            client["id"] = doc.id
                            all_clients.append(client)
                            
                    except Exception as query_error:
                        query_errors.append({
                            "departments": batch_departments,
                            "cities": batch_cities,
                            "error": str(query_error)
                        })
                        continue
        
        # Remove duplicates
        seen_ids = set()
        unique_clients = []
        for client in all_clients:
            if client["id"] not in seen_ids:
                seen_ids.add(client["id"])
                unique_clients.append(client)
        
        # Throw detailed exception if no clients found
        if len(unique_clients) == 0:
            # Fallback: Check what cities actually exist for the requested departments
            actual_cities_for_depts = set()
            try:
                for i in range(0, min(len(department_ids_list), batch_size)):
                    test_dept = department_ids_list[i]
                    test_query = (
                        db.collection("clients")
                        .where("department", "==", test_dept)
                        .limit(20)
                        .stream()
                    )
                    for doc in test_query:
                        client = doc.to_dict()
                        city = client.get('city')
                        if city:
                            actual_cities_for_depts.add(str(city).strip())
            except Exception:
                pass
            
            if actual_cities_for_depts:
                diagnostic_info["actual_cities_for_departments"] = list(actual_cities_for_depts)[:20]
            error_parts = ["No clients found matching the criteria"]
            error_parts.append(f"Requested departments: {department_ids_list}")
            error_parts.append(f"Requested cities: {cities_list}")
            error_parts.append(f"No state filter applied")
            
            if diagnostic_info["dept_mismatches"]:
                error_parts.append(f"Department IDs not in database: {diagnostic_info['dept_mismatches']}")
                error_parts.append(f"Available departments (sample): {diagnostic_info['sample_departments'][:10]}")
            
            if diagnostic_info["city_mismatches"]:
                error_parts.append(f"Cities not in database: {diagnostic_info['city_mismatches']}")
                all_cities = diagnostic_info.get("all_db_cities", diagnostic_info.get("sample_cities", []))
                error_parts.append(f"Available cities in database: {all_cities[:20]}")
            
            if query_errors:
                error_parts.append(f"Query errors occurred: {len(query_errors)}")
                # Include first few query errors for debugging
                for i, q_err in enumerate(query_errors[:3]):
                    error_parts.append(f"Query error {i+1}: {q_err.get('error', 'Unknown error')}")
            
            # Additional diagnostic: Check if any clients exist with the requested departments
            if diagnostic_info["department_matches"] == 0:
                error_parts.append("No clients found with requested departments (even without city filter)")
            
            # Show actual cities that exist for the requested departments
            if "actual_cities_for_departments" in diagnostic_info:
                actual_cities = diagnostic_info["actual_cities_for_departments"]
                error_parts.append(f"Cities that exist for requested departments: {actual_cities}")
            
            error_message = " | ".join(error_parts)
            raise Exception(error_message)
        
        return unique_clients
        
    except Exception as e:
        # Re-raise with diagnostic info if it's our custom exception
        if "No clients found" in str(e):
            raise
        # Otherwise wrap in a generic error
        raise Exception(f"Failed to fetch eligible clients: {str(e)}")


def _extract_influencer_doctors(client):
    """Extract influencer doctors from client's additional info.
    
    Args:
        client: Client dictionary with additionalInfo
        
    Returns:
        List of influencer doctor dictionaries with name, phone, email, priority
    """
    influencer_doctors = []

    # Check if client has additional info
    additional_info = client.get("additionalInfo")
    if not additional_info:
        return influencer_doctors

    # Get doctors list from additional info
    doctors = additional_info.get("doctors", [])
    if not doctors:
        return influencer_doctors

    # Filter for influencer doctors
    for doctor in doctors:
        is_influencer = doctor.get("isInfluencer", False)
        if is_influencer:
            influencer_doctors.append({
                "name": doctor.get("name", ""),
                "phone": doctor.get("phone", ""),
                "email": doctor.get("email", ""),
                # Priority now lives on the influencer doctor (A/B/C), not the client
                "priority": doctor.get("priority")
            })

    return influencer_doctors



def _fetch_target_products_simple(product_ids, db):
    """Fetch products by their IDs (simplified version matching Dart implementation).
    
    Args:
        product_ids: List of product IDs to fetch
        db: Firestore database instance
        
    Returns:
        List of product dictionaries with id added
        
    Raises:
        Exception: If no products found or query fails
    """
    if not product_ids:
        raise Exception("No product IDs provided")
    
    products = []

    try:
        for pid in product_ids:
            doc = db.collection("products").document(pid).get()
            if doc.exists:
                product = doc.to_dict()
                product["id"] = doc.id
                products.append(product)

        if not products:
            raise Exception(f"No products found for IDs: {product_ids}")

        return products
    except Exception as e:
        if "No products found" in str(e):
            raise
        raise Exception(f"Failed to fetch target products: {str(e)}")


def _create_doctor_task(plan_id, plan_data, client, product, marketing_task, doctor, db):
    """Create a task for a specific doctor and product marketing combination.
    
    Checks for existing tasks to avoid duplicates.
    Creates task matching the Flutter TaskModel structure with doctor information.
    
    Args:
        plan_id: Plan ID
        plan_data: Plan data dictionary
        client: Client dictionary
        product: Product dictionary
        marketing_task: Marketing task (string or dict)
        doctor: Doctor dictionary with name, phone, email, priority
        db: Firestore database instance
        
    Returns:
        True if task was created, False if it already exists
        
    Raises:
        Exception: If task creation fails
    """
    # Extract marketing task name for comparison
    marketing_task_name = _marketing_task_name(marketing_task)

    try:
        # Check if task already exists for this doctor + product + marketing task combination
        existing_query = (
            db.collection("tasks")
            .where("planId", "==", plan_id)
            .where("clientId", "==", client["id"])
            .where("productId", "==", product["id"])
            .where("marketingTask", "==", marketing_task_name)
            .where("doctorName", "==", doctor.get("name", ""))
            .limit(1)
            .stream()
        )
        
        if list(existing_query):
            return False

        # Create new task matching Flutter TaskModel structure with doctor info
        task_data = _build_planned_task_payload(
            plan_id,
            client["id"],
            product["id"],
            marketing_task_name,
            doctor,
        )

        task_ref = db.collection("tasks").document()
        task_ref.set(task_data)
        
        return True
        
    except Exception as e:
        raise Exception(f"Failed to create task for doctor {doctor.get('name')}, client {client.get('id')}, product {product.get('id')}: {str(e)}")


def _validate_plan_payload(data):
    """Validate the 'plan' entry of a request body and extract its task criteria.

    Args:
        data: Request body dict expected to carry a 'plan' key.

    Returns:
        Tuple of (context, error). On success `context` is a dict with keys
        planData / planId / productIds / cities / departments and `error` is
        None. On failure `context` is None and `error` is a (response, status)
        tuple ready to be returned to the caller.
    """
    plan_data = data.get("plan")
    if not plan_data:
        return None, (jsonify({
            "error": "Plan data is required",
            "success": False
        }), 400)

    plan_id = plan_data.get("id")
    if not plan_id or plan_id == "":
        return None, (jsonify({
            "error": "Plan ID is required and must not be empty",
            "success": False
        }), 400)

    # Extract product IDs from plan.targetProductSales
    target_product_sales = plan_data.get("targetProductSales", [])
    if not target_product_sales:
        return None, (jsonify({
            "error": "Plan has no target products",
            "success": False,
            "planId": plan_id
        }), 400)

    product_ids = []
    for item in target_product_sales:
        if isinstance(item, dict):
            product_id = item.get("productId")
            if product_id:
                product_ids.append(product_id)

    if not product_ids:
        return None, (jsonify({
            "error": "No effective product IDs found in targetProductSales",
            "success": False,
            "planId": plan_id
        }), 400)

    # Extract client criteria from plan
    plan_cities = plan_data.get("cities", [])
    plan_departments = plan_data.get("departmentsIds", [])

    if not plan_departments:
        return None, (jsonify({
            "error": "Plan has no departments",
            "success": False,
            "planId": plan_id
        }), 400)

    if not plan_cities:
        return None, (jsonify({
            "error": "Plan has no cities",
            "success": False,
            "planId": plan_id
        }), 400)

    return {
        "planData": plan_data,
        "planId": plan_id,
        "productIds": product_ids,
        "cities": plan_cities,
        "departments": plan_departments,
    }, None


def create_plan_tasks(data, db):
    """Create tasks based on plan model.
    
    Extracts all data from the Plan model:
    - Product IDs from plan.targetProductsSales
    - Client criteria from plan.departmentsIds and plan.cities
    
    Matches the Dart implementation behavior:
    - Checks for existing tasks to avoid duplicates
    - Creates tasks for all matching clients (no state filter)
    
    Args:
        data: Request data containing plan information
        db: Firestore database instance
        
    Returns:
        JSON response with success status and created tasks
    """
    # Extract and validate plan data
    plan, error = _validate_plan_payload(data)
    if error:
        return error

    plan_data = plan["planData"]
    plan_id = plan["planId"]
    product_ids = plan["productIds"]
    plan_cities = plan["cities"]
    plan_departments = plan["departments"]

    try:
        # Fetch products - will throw exception if not found
        products = _fetch_target_products_simple(product_ids, db)
        
        # Fetch eligible clients - will throw detailed exception if not found
        clients = _fetch_eligible_clients(plan_departments, plan_cities, db)
        
        # Update plan with matching client IDs
        client_ids = [client["id"] for client in clients]
        try:
            plan_ref = db.collection("plans").document(plan_id)
            plan_ref.update({
                "clientsIds": client_ids,
                "updatedAt": firestore.SERVER_TIMESTAMP  # type: ignore[attr-defined]
            })
        except Exception as update_error:
            # Log error but don't fail the task creation
            print(f"⚠️ Warning: Failed to update plan with client IDs: {str(update_error)}")
        
        # Create tasks for influencer doctors
        created_count = 0
        skipped_count = 0
        task_errors = []
        clients_without_doctors = 0
        total_influencer_doctors = 0
        
        for client in clients:
            # Extract influencer doctors from client's additional info
            influencer_doctors = _extract_influencer_doctors(client)

            # If no influencer doctors, create tasks without doctor info
            if not influencer_doctors:
                clients_without_doctors += 1
                doctors_to_process = [{"name": "", "phone": "", "email": ""}]
            else:
                doctors_to_process = influencer_doctors

            total_influencer_doctors += len(influencer_doctors)

            # Get client's department for product matching
            client_department = client.get("department")

            # Loop through each doctor (or empty doctor placeholder)
            for doctor in doctors_to_process:
                for product in products:
                    # Only create tasks if client's department matches product's departments
                    product_departments = product.get("departmentsIds", [])
                    if client_department and product_departments and client_department not in product_departments:
                        continue

                    marketing_tasks = product.get("marketingTasks", [])
                    if not marketing_tasks:
                        continue
                    
                    for marketing_task in marketing_tasks:
                        try:
                            created = _create_doctor_task(
                                plan_id, 
                                plan_data, 
                                client, 
                                product, 
                                marketing_task, 
                                doctor, 
                                db
                            )
                            if created:
                                created_count += 1
                            else:
                                skipped_count += 1
                        except Exception as task_error:
                            task_errors.append({
                                "clientId": client.get("id"),
                                "doctorName": doctor.get("name"),
                                "productId": product.get("id"),
                                "error": str(task_error)
                            })
                            continue
        
        response = {
            "success": True,
            "message": f"Created {created_count} tasks for {total_influencer_doctors} influencer doctors, skipped {skipped_count} duplicates",
            "tasksCreated": created_count,
            "tasksSkipped": skipped_count,
            "planId": plan_id,
            "clientsProcessed": len(clients),
            "clientsIds": client_ids,
            "clientsWithoutInfluencerDoctors": clients_without_doctors,
            "influencerDoctorsProcessed": total_influencer_doctors,
            "productsProcessed": len(products)
        }
        
        if task_errors:
            response["taskErrors"] = task_errors
            response["taskErrorCount"] = len(task_errors)
        
        return jsonify(response)
        
    except Exception as e:
        error_msg = str(e)
        return jsonify({
            "error": error_msg,
            "success": False,
            "planId": plan_id,
            "details": {
                "departments": plan_departments,
                "cities": plan_cities,
                "productIds": product_ids
            }
        }), 400


def regenerate_plan_tasks(data, db):
    """Wipe and recreate a plan's tasks after the plan was edited.

    Called when a planner edits a plan's targeting data (cities, departments or
    target products). Tasks generated from the previous data would otherwise
    live on forever, so the whole task set is rebuilt from the new plan:

      - Tasks holding real fieldwork are protected and left untouched:
        completed tasks, and any task carrying a visitResult.
      - Every other task of the plan is hard-deleted.
      - The full task set is then regenerated from the plan's current cities /
        departments / products, skipping combinations already covered by a
        protected task so nothing is duplicated.

    Deletes and creates are collected first and committed together in batches of
    at most 499 writes, so a mid-way failure cannot leave the plan emptied.

    Args:
        data: Request body dict with a 'plan' key.
        db:   Firestore database instance.

    Returns:
        JSON response with the delete/create counts.
    """
    plan, error = _validate_plan_payload(data)
    if error:
        return error

    plan_id = plan["planId"]
    product_ids = plan["productIds"]
    plan_cities = plan["cities"]
    plan_departments = plan["departments"]

    try:
        # --- Phase 1: partition the plan's existing tasks ---
        protected_keys = set()
        protected_client_ids = set()
        protected_count = 0
        doomed_refs = []

        for task_doc in db.collection("tasks").where("planId", "==", plan_id).stream():
            task = task_doc.to_dict()
            if _task_is_protected(task):
                protected_count += 1
                protected_keys.add(
                    _task_identity_key(
                        plan_id,
                        task.get("clientId"),
                        task.get("productId"),
                        task.get("marketingTask"),
                        task.get("doctorName"),
                    )
                )
                client_id = task.get("clientId")
                if client_id:
                    protected_client_ids.add(client_id)
            else:
                doomed_refs.append(db.collection("tasks").document(task_doc.id))

        # --- Phase 2: rebuild the desired task set from the plan's new data ---
        # Both raise a detailed exception when nothing matches.
        products = _fetch_target_products_simple(product_ids, db)
        clients = _fetch_eligible_clients(plan_departments, plan_cities, db)

        client_ids = [client["id"] for client in clients]

        batch_ops = [("delete", ref, None) for ref in doomed_refs]
        created_count = 0
        skipped_count = 0
        clients_without_doctors = 0
        total_influencer_doctors = 0

        for client in clients:
            influencer_doctors = _extract_influencer_doctors(client)

            # If no influencer doctors, create tasks without doctor info
            if not influencer_doctors:
                clients_without_doctors += 1
                doctors_to_process = [{"name": "", "phone": "", "email": ""}]
            else:
                doctors_to_process = influencer_doctors

            total_influencer_doctors += len(influencer_doctors)

            client_department = client.get("department")

            for doctor in doctors_to_process:
                for product in products:
                    # Only create tasks if client's department matches product's departments
                    product_departments = product.get("departmentsIds", [])
                    if client_department and product_departments and client_department not in product_departments:
                        continue

                    marketing_tasks = product.get("marketingTasks", [])
                    if not marketing_tasks:
                        continue

                    for marketing_task in marketing_tasks:
                        marketing_task_name = _marketing_task_name(marketing_task)
                        key = _task_identity_key(
                            plan_id,
                            client["id"],
                            product["id"],
                            marketing_task_name,
                            doctor.get("name", ""),
                        )
                        # A protected task already covers this combination.
                        if key in protected_keys:
                            skipped_count += 1
                            continue

                        task_data = _build_planned_task_payload(
                            plan_id,
                            client["id"],
                            product["id"],
                            marketing_task_name,
                            doctor,
                        )
                        batch_ops.append(
                            ("set", db.collection("tasks").document(), task_data)
                        )
                        created_count += 1

        # --- Phase 3: commit deletes and creates together (chunked at <= 499) ---
        BATCH_LIMIT = 499
        for i in range(0, len(batch_ops), BATCH_LIMIT):
            chunk = batch_ops[i:i + BATCH_LIMIT]
            batch = db.batch()
            for op, ref, op_data in chunk:
                if op == "delete":
                    batch.delete(ref)
                elif op == "set":
                    batch.set(ref, op_data)
            batch.commit()

        # --- Phase 4: refresh the plan's denormalised counters ---
        # Clients of protected tasks stay in clientsIds even when they no longer
        # match the plan's targeting: the dashboards use clientsIds as the
        # denominator for completed-client KPIs, so dropping them would skew it.
        matched_client_ids = set(client_ids)
        merged_client_ids = client_ids + [
            cid for cid in sorted(protected_client_ids) if cid not in matched_client_ids
        ]
        tasks_count = protected_count + created_count
        try:
            plan_ref = db.collection("plans").document(plan_id)
            plan_ref.update({
                "clientsIds": merged_client_ids,
                "tasksCount": tasks_count,
                "updatedAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
            })
        except Exception as update_error:
            # Log but don't fail: the tasks themselves are already correct.
            print(f"⚠️ Warning: Failed to update plan counters: {str(update_error)}")

        return jsonify({
            "success": True,
            "message": (
                f"Regenerated plan tasks: {len(doomed_refs)} deleted, "
                f"{created_count} created, {protected_count} protected"
            ),
            "planId": plan_id,
            "tasksDeleted": len(doomed_refs),
            "tasksProtected": protected_count,
            "tasksCreated": created_count,
            "tasksSkipped": skipped_count,
            "tasksCount": tasks_count,
            "clientsProcessed": len(clients),
            "clientsIds": merged_client_ids,
            "clientsWithoutInfluencerDoctors": clients_without_doctors,
            "influencerDoctorsProcessed": total_influencer_doctors,
            "productsProcessed": len(products),
        })

    except Exception as e:
        error_msg = str(e)
        print(f"Failed to regenerate tasks for plan {plan_id}: {error_msg}")
        print(traceback.format_exc())
        return jsonify({
            "error": error_msg,
            "success": False,
            "planId": plan_id,
            "details": {
                "departments": plan_departments,
                "cities": plan_cities,
                "productIds": product_ids
            }
        }), 400


def reconcile_client_tasks(data, db):
    """Reconcile a client's planned tasks against its current influencer doctors.

    For each call:
      - R1: Completed tasks are never modified or deleted.
      - R2: Not-completed tasks whose doctor's priority changed are updated.
      - R3: Not-completed tasks already at the correct priority are left alone.
      - R4: Not-completed tasks whose doctor was removed are soft-deleted.
      - R0: Tasks with empty doctorName are not touched.
      - R5: Missing tasks (new doctor, new plan, new product) are created.

    All writes are committed in a single Firestore batch (chunked at ≤ 500 ops).
    Completed tasks are never modified or deleted.

    Also routes back-compat calls from the old 'createTasksForNewClient' action.

    Args:
        data: Request body dict with 'client' key.
        db:   Firestore database instance.

    Returns:
        JSON response conforming to contracts/sync-tasks-for-client.md.
    """
    client_data = data.get("client")
    if not client_data:
        return jsonify({"error": "Client data is required", "success": False}), 400

    client_id = client_data.get("id")
    if not client_id:
        return jsonify({"error": "Client ID is required", "success": False}), 400

    client_city = client_data.get("city")
    client_department = client_data.get("department")

    if not client_city:
        return jsonify({"error": "Client city is required", "success": False, "clientId": client_id}), 400
    if not client_department:
        return jsonify({"error": "Client department is required", "success": False, "clientId": client_id}), 400

    # --- Counters ---
    tasks_created = 0
    tasks_updated = 0
    tasks_deleted = 0
    tasks_skipped = 0
    completed_skipped = 0
    matching_plans_count = 0

    try:
        # Build desired influencer map: { doctor_name: priority }
        desired = _extract_influencer_priority_map(client_data)

        # Fetch all existing tasks for this client, then drop soft-deleted ones
        # in memory. Filtering reviewState here (instead of a second `!=` Firestore
        # filter) avoids a composite-index requirement on (clientId, reviewState)
        # and still includes legacy tasks that have no reviewState field.
        existing_tasks = [
            task_doc
            for task_doc in db.collection("tasks")
            .where("clientId", "==", client_id)
            .stream()
            if task_doc.to_dict().get("reviewState") != "deleted"
        ]

        # --- Phase 1: Reconcile existing tasks (R1-R4 + R0) ---
        # Collect all batch operations; chunk at 499 writes
        batch_ops = []   # list of (op, ref, data)

        for task_doc in existing_tasks:
            task = task_doc.to_dict()
            task_ref = db.collection("tasks").document(task_doc.id)
            doctor_name = task.get("doctorName", "")

            # R0: empty doctorName → out of scope
            if not doctor_name:
                continue

            # R1: completed tasks are immutable
            if _is_task_completed(task):
                completed_skipped += 1
                continue

            if doctor_name in desired:
                current_priority = task.get("priority")
                new_priority = desired[doctor_name]
                if current_priority != new_priority:
                    # R2: priority drift → update
                    batch_ops.append(
                        ("update", task_ref, {
                            "priority": new_priority,
                            "updatedAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
                        })
                    )
                    tasks_updated += 1
                # else R3: no change needed
            else:
                # R4: doctor removed → soft-delete
                batch_ops.append(
                    ("update", task_ref, {
                        "reviewState": "deleted",
                        "updatedAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
                    })
                )
                tasks_deleted += 1

        # --- Phase 2: Create missing tasks (R5) ---
        # Find matching active plans (same logic as create_tasks_for_new_client)
        matching_plans = []
        try:
            now = datetime.now(timezone.utc)
            plans_query = (
                db.collection("plans")
                .where("cities", "array_contains", client_city)
                .stream()
            )
            for plan_doc in plans_query:
                plan = plan_doc.to_dict()
                plan["id"] = plan_doc.id

                # Skip expired plans
                plan_end_date = plan.get("endDate")
                if plan_end_date:
                    if hasattr(plan_end_date, 'timestamp'):
                        end_dt = datetime.fromtimestamp(plan_end_date.timestamp(), tz=timezone.utc)
                    elif isinstance(plan_end_date, datetime):
                        end_dt = plan_end_date if plan_end_date.tzinfo else plan_end_date.replace(tzinfo=timezone.utc)
                    else:
                        end_dt = None
                    if end_dt and end_dt < now:
                        continue

                plan_departments = plan.get("departmentsIds", [])
                if client_department not in plan_departments:
                    continue

                matching_plans.append(plan)

        except Exception as query_error:
            return jsonify({
                "error": f"Failed to query plans: {str(query_error)}",
                "success": False,
                "clientId": client_id,
            }), 500

        matching_plans_count = len(matching_plans)

        # Build the influencer doctors list for R5 (create path)
        if desired:
            doctors_to_process = [
                {"name": name, "priority": priority}
                for name, priority in desired.items()
            ]
        else:
            doctors_to_process = [{"name": "", "priority": "C"}]

        for plan in matching_plans:
            plan_id = plan.get("id")
            if not plan_id:
                continue

            target_product_sales = plan.get("targetProductSales", [])
            product_ids = [
                item["productId"] for item in target_product_sales
                if isinstance(item, dict) and item.get("productId")
            ]
            if not product_ids:
                continue

            for product_id in product_ids:
                product_doc = db.collection("products").document(product_id).get()
                if not product_doc.exists:
                    continue
                product = product_doc.to_dict()
                product["id"] = product_doc.id

                product_departments = product.get("departmentsIds", [])
                if client_department not in product_departments:
                    continue

                marketing_tasks = product.get("marketingTasks", [])
                if not marketing_tasks:
                    continue

                for doctor in doctors_to_process:
                    for marketing_task in marketing_tasks:
                        marketing_task_name = (
                            marketing_task.get("name") if isinstance(marketing_task, dict)
                            else str(marketing_task)
                        )
                        existing_q = list(
                            db.collection("tasks")
                            .where("planId", "==", plan_id)
                            .where("clientId", "==", client_id)
                            .where("productId", "==", product["id"])
                            .where("marketingTask", "==", marketing_task_name)
                            .where("doctorName", "==", doctor.get("name", ""))
                            .stream()
                        )
                        if existing_q:
                            tasks_skipped += 1
                            continue

                        # R5: create new task
                        task_data = {
                            "taskType": "planned",
                            "assignedToId": None,
                            "planId": plan_id,
                            "clientId": client_id,
                            "targetDate": None,
                            "productId": product["id"],
                            "status": "pending",
                            "cancelReason": None,
                            "reviewState": "approved",
                            "visitResult": None,
                            "priority": doctor.get("priority", "C"),
                            "note": None,
                            "doctorName": doctor.get("name", ""),
                            "createdAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
                            "updatedAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
                            "marketingTask": marketing_task_name,
                        }
                        new_ref = db.collection("tasks").document()
                        batch_ops.append(("set", new_ref, task_data))
                        tasks_created += 1

            # Update plan.clientsIds
            plan_clients_ids = plan.get("clientsIds", [])
            if client_id not in plan_clients_ids:
                plan_ref = db.collection("plans").document(plan_id)
                batch_ops.append(
                    ("update", plan_ref, {
                        "clientsIds": firestore.ArrayUnion([client_id]),  # type: ignore[attr-defined]
                    })
                )

        # --- Commit all batch ops (chunked at ≤ 499 per batch) ---
        BATCH_LIMIT = 499
        for i in range(0, max(1, len(batch_ops)), BATCH_LIMIT):
            chunk = batch_ops[i:i + BATCH_LIMIT]
            if not chunk:
                break
            batch = db.batch()
            for op, ref, op_data in chunk:
                if op == "update":
                    batch.update(ref, op_data)
                elif op == "set":
                    batch.set(ref, op_data)
            batch.commit()

        return jsonify({
            "success": True,
            "clientId": client_id,
            "message": (
                f"Reconciled tasks for client: "
                f"{tasks_created} created, {tasks_updated} updated, "
                f"{tasks_deleted} deleted, {tasks_skipped} skipped"
            ),
            "tasksCreated": tasks_created,
            "tasksUpdated": tasks_updated,
            "tasksDeleted": tasks_deleted,
            "tasksSkipped": tasks_skipped,
            "completedSkipped": completed_skipped,
            "matchingPlans": matching_plans_count,
        })

    except Exception as e:
        error_msg = f"Failed to reconcile tasks for client: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({
            "error": error_msg,
            "success": False,
            "clientId": client_id,
        }), 500


def create_tasks_for_new_client(data, db):
    """Create tasks for a newly created client based on matching plans.
    
    This function:
    1. Takes the new client data
    2. Finds all plans where:
       - client.city in plan.cities
       - client.department in plan.departmentsIds
       - client.id NOT in plan.clientsIds (to avoid duplicate task creation)
    3. For each matching plan:
       - Gets productIds from plan.targetProductSales
       - Fetches those products from Firestore
       - Filters products where client.department is in product.departmentsIds
       - Gets influencer doctors from client.additionalInfo
       - Creates tasks for each doctor, product, and marketing task combination
       - Updates the plan to add client.id to plan.clientsIds array
    
    Args:
        data: Request data containing client information
        db: Firestore database instance
        
    Returns:
        JSON response with success status and created tasks summary
    """
    # Extract and validate client data
    client_data = data.get("client")
    if not client_data:
        return jsonify({
            "error": "Client data is required",
            "success": False
        }), 400
    
    client_id = client_data.get("id")
    if not client_id or client_id == "":
        return jsonify({
            "error": "Client ID is required and must not be empty",
            "success": False
        }), 400
    
    client_city = client_data.get("city")
    client_department = client_data.get("department")
    
    if not client_city:
        return jsonify({
            "error": "Client city is required",
            "success": False,
            "clientId": client_id
        }), 400
    
    if not client_department:
        return jsonify({
            "error": "Client department is required",
            "success": False,
            "clientId": client_id
        }), 400
    
  
    
    try:
        # Find matching plans where:
        # - client.city in plan.cities
        # - client.department in plan.departmentsIds
        matching_plans = []
        
        try:
            # Query plans where client's city is in plan.cities (server-side filter)
            # Firestore only supports one array_contains per query, so filter department in Python
            plans_query = (
                db.collection("plans")
                .where("cities", "array_contains", client_city)
                .stream()
            )

            now = datetime.now(timezone.utc)

            for plan_doc in plans_query:
                plan = plan_doc.to_dict()
                plan["id"] = plan_doc.id

                # Skip expired plans (endDate in the past)
                plan_end_date = plan.get("endDate")
                if plan_end_date:
                    if hasattr(plan_end_date, 'timestamp'):
                        end_dt = datetime.fromtimestamp(plan_end_date.timestamp(), tz=timezone.utc)
                    elif isinstance(plan_end_date, datetime):
                        end_dt = plan_end_date if plan_end_date.tzinfo else plan_end_date.replace(tzinfo=timezone.utc)
                    else:
                        end_dt = None
                    if end_dt and end_dt < now:
                        continue

                plan_departments = plan.get("departmentsIds", [])
                plan_clients_ids = plan.get("clientsIds", [])

                # Check if client's department matches the plan
                # AND client is not already in the plan's clientsIds
                if (client_department in plan_departments and
                    client_id not in plan_clients_ids):
                    matching_plans.append(plan)
        
        except Exception as query_error:
            return jsonify({
                "error": f"Failed to query plans: {str(query_error)}",
                "success": False,
                "clientId": client_id
            }), 500
        
        if not matching_plans:
            return jsonify({
                "success": True,
                "message": "No matching plans found for this client",
                "clientId": client_id,
                "clientCity": client_city,
                "clientDepartment": client_department,
                "tasksCreated": 0
            })
        
        # Extract influencer doctors from client
        influencer_doctors = _extract_influencer_doctors(client_data)

        # If no influencer doctors, create tasks without doctor info
        if not influencer_doctors:
            doctors_to_process = [{"name": "", "phone": "", "email": ""}]
        else:
            doctors_to_process = influencer_doctors

        # Create tasks for each matching plan
        total_created = 0
        total_skipped = 0
        task_errors = []
        plans_processed = []

        for plan in matching_plans:
            plan_id = plan.get("id")
            if not plan_id:
                continue

            plan_created = 0
            plan_skipped = 0
            eligible_products = []

            # Get product IDs from plan.targetProductSales
            target_product_sales = plan.get("targetProductSales", [])

            product_ids = []
            for item in target_product_sales:
                if isinstance(item, dict):
                    product_id = item.get("productId")
                    if product_id:
                        product_ids.append(product_id)

            # Fetch products and filter by client department
            if product_ids:
                try:
                    for product_id in product_ids:
                        product_doc = db.collection("products").document(product_id).get()
                        if not product_doc.exists:
                            continue

                        product = product_doc.to_dict()
                        product["id"] = product_doc.id

                        # Check if client's department is in product's departmentsIds
                        product_departments = product.get("departmentsIds", [])
                        if client_department in product_departments:
                            eligible_products.append(product)

                    # Create tasks for each doctor (or placeholder), product, and marketing task
                    for doctor in doctors_to_process:
                        for product in eligible_products:
                            marketing_tasks = product.get("marketingTasks", [])
                            if not marketing_tasks:
                                continue

                            for marketing_task in marketing_tasks:
                                try:
                                    created = _create_doctor_task(
                                        plan_id,
                                        plan,
                                        client_data,
                                        product,
                                        marketing_task,
                                        doctor,
                                        db
                                    )
                                    if created:
                                        plan_created += 1
                                    else:
                                        plan_skipped += 1
                                except Exception as task_error:
                                    task_errors.append({
                                        "planId": plan_id,
                                        "clientId": client_id,
                                        "doctorName": doctor.get("name"),
                                        "productId": product.get("id"),
                                        "error": str(task_error)
                                    })
                                    continue

                    total_created += plan_created
                    total_skipped += plan_skipped

                except Exception as plan_error:
                    task_errors.append({
                        "planId": plan_id,
                        "error": f"Failed to process plan: {str(plan_error)}"
                    })

            # Always update plan to add client ID to clientsIds array
            # even if no eligible products matched, to prevent re-processing
            try:
                plan_ref = db.collection("plans").document(plan_id)
                plan_ref.update({
                    "clientsIds": firestore.ArrayUnion([client_id])  # type: ignore[attr-defined]
                })
            except Exception as update_error:
                task_errors.append({
                    "planId": plan_id,
                    "error": f"Failed to update plan clientsIds: {str(update_error)}"
                })

            plans_processed.append({
                "planId": plan_id,
                "planTitle": plan.get("title", ""),
                "tasksCreated": plan_created,
                "tasksSkipped": plan_skipped,
                "productsProcessed": len(eligible_products)
            })
        
        response = {
            "success": True,
            "message": f"Created {total_created} tasks for client across {len(plans_processed)} plans",
            "clientId": client_id,
            "tasksCreated": total_created,
            "tasksSkipped": total_skipped,
            "matchingPlans": len(matching_plans),
            "plansProcessed": plans_processed,
            "influencerDoctorsCount": len(influencer_doctors)
        }
        
        if task_errors:
            response["taskErrors"] = task_errors
            response["taskErrorCount"] = len(task_errors)
        
        return jsonify(response)
    
    except Exception as e:
        error_msg = f"Failed to create tasks for new client: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({
            "error": error_msg,
            "success": False,
            "clientId": client_id
        }), 500


def create_tasks_from_product(data, db):
    """Create tasks for a product added to a plan.
    
    This function:
    1. Takes productId, planId, and targetSales from input
    2. Gets the plan by its ID from plans collection
    3. Checks if the product is already in plan.targetProductSales
       - If yes, returns error "can't add this product to this plan, it's added before"
    4. Gets the product by its ID from products collection
    5. Gets all clients from clients collection where:
       - Client's department is in product's departmentsIds
       - Client's city is in plan's cities
    6. For each matching client:
       - Gets influencer doctors from client
       - Creates tasks for each doctor, product, and marketing task combination
    7. Updates the plan:
       - Adds {productId, targetSales} to targetProductSales
       - Adds client IDs to clientsIds (only if not already present)
    
    Args:
        data: Request data containing productId, planId, and targetSales
        db: Firestore database instance
        
    Returns:
        JSON response with success status and created tasks summary
    """
    # Extract and validate input data
    product_id = data.get("productId")
    plan_id = data.get("planId")
    target_sales = data.get("targetSales")
    
    if not product_id or product_id == "":
        return jsonify({
            "error": "Product ID is required and must not be empty",
            "success": False
        }), 400
    
    if not plan_id or plan_id == "":
        return jsonify({
            "error": "Plan ID is required and must not be empty",
            "success": False
        }), 400
    
    if target_sales is None:
        return jsonify({
            "error": "Target sales is required",
            "success": False
        }), 400
    
    try:
        # Get plan by ID
        plan_doc = db.collection("plans").document(plan_id).get()
        if not plan_doc.exists:
            return jsonify({
                "error": f"Plan with ID {plan_id} not found",
                "success": False,
                "planId": plan_id
            }), 404
        
        plan = plan_doc.to_dict()
        plan["id"] = plan_doc.id
        
        # Check if product is already in plan.targetProductSales
        target_product_sales = plan.get("targetProductSales", [])
        for item in target_product_sales:
            if isinstance(item, dict):
                existing_product_id = item.get("productId")
                if existing_product_id == product_id:
                    return jsonify({
                        "error": "Can't add this product to this plan, it's added before",
                        "success": False,
                        "planId": plan_id,
                        "productId": product_id
                    }), 400
        
        # Get product by ID
        product_doc = db.collection("products").document(product_id).get()
        if not product_doc.exists:
            return jsonify({
                "error": f"Product with ID {product_id} not found",
                "success": False,
                "productId": product_id
            }), 404
        
        product = product_doc.to_dict()
        product["id"] = product_doc.id
        
        # Get product's departmentsIds
        product_departments = product.get("departmentsIds", [])
        if not product_departments:
            return jsonify({
                "success": True,
                "message": "Product has no departments. No tasks created.",
                "productId": product_id,
                "tasksCreated": 0
            })
        
        # Get plan's cities
        plan_cities = plan.get("cities", [])
        if not plan_cities:
            return jsonify({
                "success": True,
                "message": "Plan has no cities. No tasks created.",
                "planId": plan_id,
                "tasksCreated": 0
            })
        
        # Get all clients where:
        # - Client's department is in BOTH product's departmentsIds AND plan's departmentsIds
        # - Client's city is in plan's cities
        plan_departments = plan.get("departmentsIds", [])
        # Only target departments that exist in both the product and the plan
        target_departments = [d for d in product_departments if d in plan_departments]

        if not target_departments:
            return jsonify({
                "success": True,
                "message": "No overlapping departments between product and plan. No tasks created.",
                "planId": plan_id,
                "productId": product_id,
                "tasksCreated": 0
            })

        eligible_clients = []

        try:
            # Query clients by department (handle Firebase whereIn limitation)
            department_batches = []
            batch_size = 10
            for i in range(0, len(target_departments), batch_size):
                batch = target_departments[i:i + batch_size]
                department_batches.append(batch)
            
            for dept_batch in department_batches:
                clients_query = (
                    db.collection("clients")
                    .where("department", "in", dept_batch)
                    .stream()
                )
                
                for client_doc in clients_query:
                    client = client_doc.to_dict()
                    client["id"] = client_doc.id
                    
                    # Check if client's city is in plan's cities
                    client_city = client.get("city")
                    if client_city and client_city in plan_cities:
                        eligible_clients.append(client)
        
        except Exception as query_error:
            return jsonify({
                "error": f"Failed to query clients: {str(query_error)}",
                "success": False,
                "planId": plan_id,
                "productId": product_id
            }), 500
        
        if not eligible_clients:
            return jsonify({
                "success": True,
                "message": "No eligible clients found matching the criteria",
                "planId": plan_id,
                "productId": product_id,
                "tasksCreated": 0
            })
        
        # Get product's marketing tasks
        marketing_tasks = product.get("marketingTasks", [])
        if not marketing_tasks:
            return jsonify({
                "success": True,
                "message": "Product has no marketing tasks. No tasks created.",
                "productId": product_id,
                "eligibleClients": len(eligible_clients),
                "tasksCreated": 0
            })
        
        # Create tasks for each client
        total_created = 0
        total_skipped = 0
        task_errors = []
        clients_processed = []
        new_client_ids = []
        
        for client in eligible_clients:
            client_id = client.get("id")
            if not client_id:
                continue
            
            # Extract influencer doctors from client
            influencer_doctors = _extract_influencer_doctors(client)

            # If no influencer doctors, create tasks without doctor info
            if not influencer_doctors:
                doctors_to_process = [{"name": "", "phone": "", "email": ""}]
            else:
                doctors_to_process = influencer_doctors

            client_created = 0
            client_skipped = 0

            # Create tasks for each doctor (or placeholder), product, and marketing task combination
            for doctor in doctors_to_process:
                for marketing_task in marketing_tasks:
                    try:
                        created = _create_doctor_task(
                            plan_id,
                            plan,
                            client,
                            product,
                            marketing_task,
                            doctor,
                            db
                        )
                        if created:
                            client_created += 1
                        else:
                            client_skipped += 1
                    except Exception as task_error:
                        task_errors.append({
                            "clientId": client_id,
                            "doctorName": doctor.get("name"),
                            "productId": product_id,
                            "error": str(task_error)
                        })
                        continue
            
            total_created += client_created
            total_skipped += client_skipped
            
            clients_processed.append({
                "clientId": client_id,
                "tasksCreated": client_created,
                "tasksSkipped": client_skipped,
                "doctorsCount": len(influencer_doctors)
            })
            
            # Track client ID for updating plan.clientsIds
            new_client_ids.append(client_id)
        
        # Update plan's targetProductSales
        try:
            plan_ref = db.collection("plans").document(plan_id)
            current_target_product_sales = plan.get("targetProductSales", [])
            updated_target_product_sales = list(current_target_product_sales)
            updated_target_product_sales.append({
                "productId": product_id,
                "targetSales": target_sales
            })
            plan_ref.update({
                "targetProductSales": updated_target_product_sales
            })
        except Exception as update_error:
            return jsonify({
                "error": f"Failed to update plan targetProductSales: {str(update_error)}",
                "success": False,
                "planId": plan_id,
                "tasksCreated": total_created
            }), 500
        
        # Update plan's clientsIds (only add clients that are not already in the list)
        clients_to_add = []
        try:
            plan_clients_ids = plan.get("clientsIds", [])
            
            for client_id in new_client_ids:
                if client_id not in plan_clients_ids:
                    clients_to_add.append(client_id)
            
            if clients_to_add:
                plan_ref = db.collection("plans").document(plan_id)
                plan_ref.update({
                    "clientsIds": firestore.ArrayUnion(clients_to_add)  # type: ignore[attr-defined]
                })
        except Exception as update_error:
            # Log error but don't fail the entire operation
            task_errors.append({
                "error": f"Failed to update plan clientsIds: {str(update_error)}"
            })
        
        response = {
            "success": True,
            "message": f"Created {total_created} tasks for product across {len(clients_processed)} clients",
            "planId": plan_id,
            "productId": product_id,
            "targetSales": target_sales,
            "tasksCreated": total_created,
            "tasksSkipped": total_skipped,
            "eligibleClients": len(eligible_clients),
            "clientsProcessed": clients_processed,
            "newClientsAddedToPlan": len(clients_to_add)
        }
        
        if task_errors:
            response["taskErrors"] = task_errors
            response["taskErrorCount"] = len(task_errors)
        
        return jsonify(response)
    
    except Exception as e:
        error_msg = f"Failed to create tasks from product: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({
            "error": error_msg,
            "success": False,
            "planId": plan_id,
            "productId": product_id
        }), 500



def get_task_stats(decoded_token, db):
    """Get task statistics aggregated by date.
    
    Query all tasks where user is a sales representative, group them by date, 
    and return count for each date.
    
    Args:
        decoded_token: Decoded Firebase Auth token
        db: Firestore database instance
        
    Returns:
        JSON response with list of stats: [{date: 'YYYY-MM-DD', count: N}, ...]
    """
    try:
        uid = decoded_token.get("uid")
        if not uid:
            return jsonify({
                "error": "User ID required",
                "success": False
            }), 400

        # Query tasks where user is assigned to
        # We filter targetDate != None and reviewState != deleted in memory to avoid complex composite index requirements
        tasks_query = db.collection("tasks").where("reviewState", "!=", "deleted").stream()

        # Dictionary to store counts: date_str -> count
        stats_map = {}

        for doc in tasks_query:
            task = doc.to_dict()
            target_date = task.get("targetDate")
            reviewState = task.get("reviewState")

            if not target_date or reviewState == "deleted":
                continue
                
            # Parse date
            date_key = None
            
            if isinstance(target_date, datetime):
                # Format as YYYY-MM-DD
                date_key = target_date.strftime("%Y-%m-%d")
            elif isinstance(target_date, str):
                # Try to parse string
                try:
                    # Handle ISO format variants
                    if "T" in target_date:
                        date_obj = datetime.fromisoformat(target_date.replace("Z", "+00:00"))
                        date_key = date_obj.strftime("%Y-%m-%d")
                    else:
                        # Assume simple date string, maybe take first 10 chars
                        date_key = target_date[:10]
                except Exception:
                    # Fallback or ignore invalid formats
                    continue
            elif isinstance(target_date, int):
                # Timestamp in milliseconds
                try:
                    date_obj = datetime.fromtimestamp(target_date / 1000.0)
                    date_key = date_obj.strftime("%Y-%m-%d")
                except Exception:
                    continue
            
            if date_key:
                stats_map[date_key] = stats_map.get(date_key, 0) + 1
        
        # Convert map to list of objects
        result = []
        for date_str, count in stats_map.items():
            result.append({
                "date": date_str,
                "count": count
            })
            
        # Sort by date
        result.sort(key=lambda x: x["date"])
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        print(f"Error getting task stats: {str(e)}")
        # trace = traceback.format_exc()
        # print(trace)
        return jsonify({
            "error": f"Failed to get stats: {str(e)}",
            "success": False
        }), 500


def get_all_tasks_stats(db):
    """Get statistics for ALL tasks aggregated by date.
    
    Query all tasks in the system, group them by date, 
    and return count for each date. This is an admin function.
    
    Args:
        db: Firestore database instance
        
    Returns:
        JSON response with list of stats: [{date: 'YYYY-MM-DD', count: N}, ...]
    """
    try:
        # Query ALL tasks (no user filter)
        # We filter targetDate != None in memory to avoid complex composite index requirements
        tasks_query = db.collection("tasks").stream()
        
        # Dictionary to store counts: date_str -> count
        stats_map = {}
        
        for doc in tasks_query:
            task = doc.to_dict()
            target_date = task.get("targetDate")
          

            if not target_date :
                continue
                
            # Parse date
            date_key = None
            
            if isinstance(target_date, datetime):
                # Format as YYYY-MM-DD
                date_key = target_date.strftime("%Y-%m-%d")
            elif isinstance(target_date, str):
                # Try to parse string
                try:
                    # Handle ISO format variants
                    if "T" in target_date:
                        date_obj = datetime.fromisoformat(target_date.replace("Z", "+00:00"))
                        date_key = date_obj.strftime("%Y-%m-%d")
                    else:
                        # Assume simple date string, maybe take first 10 chars
                        date_key = target_date[:10]
                except Exception:
                    # Fallback or ignore invalid formats
                    continue
            elif isinstance(target_date, int):
                # Timestamp in milliseconds
                try:
                    date_obj = datetime.fromtimestamp(target_date / 1000.0)
                    date_key = date_obj.strftime("%Y-%m-%d")
                except Exception:
                    continue
            
            if date_key:
                stats_map[date_key] = stats_map.get(date_key, 0) + 1
        
        # Convert map to list of objects
        result = []
        for date_str, count in stats_map.items():
            result.append({
                "date": date_str,
                "count": count
            })
            
        # Sort by date
        result.sort(key=lambda x: x["date"])
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        print(f"Error getting all tasks stats: {str(e)}")
        # trace = traceback.format_exc()
        # print(trace)
        return jsonify({
            "error": f"Failed to get stats: {str(e)}",
            "success": False
        }), 500


def get_completed_tasks_status(db):
    """Get completed task counts grouped by user, product, and client.

    Loops through all tasks, filters completed ones (status == "مكتمل"),
    and returns counts grouped by assignedToId, productId, and clientId.

    Args:
        db: Firestore database instance

    Returns:
        JSON response with:
        {
            "users": [{"id": "userId", "count": N}, ...],
            "products": [{"id": "productId", "count": N}, ...],
            "clients": [{"id": "clientId", "count": N}, ...]
        }
    """
    try:
        tasks_query = db.collection("tasks").stream()

        users_map = {}
        products_map = {}
        clients_map = {}
        total_completed = 0

        for doc in tasks_query:
            task = doc.to_dict()
            status = task.get("status")
            review_state = task.get("reviewState")

            # Only count completed tasks that are not deleted
            if status not in ("مكتمل", "completed") or review_state == "deleted":
                continue

            total_completed += 1

            # Count by user
            user_id = task.get("assignedToId")
            if user_id:
                users_map[user_id] = users_map.get(user_id, 0) + 1

            # Count by product
            product_id = task.get("productId")
            if product_id:
                products_map[product_id] = products_map.get(product_id, 0) + 1

            # Count by client
            client_id = task.get("clientId")
            if client_id:
                clients_map[client_id] = clients_map.get(client_id, 0) + 1

        users_list = [{"id": uid, "count": count} for uid, count in users_map.items()]
        products_list = [{"id": pid, "count": count} for pid, count in products_map.items()]
        clients_list = [{"id": cid, "count": count} for cid, count in clients_map.items()]

        # Sort by count descending
        users_list.sort(key=lambda x: x["count"], reverse=True)
        products_list.sort(key=lambda x: x["count"], reverse=True)
        clients_list.sort(key=lambda x: x["count"], reverse=True)

        return jsonify({
            "success": True,
            "totalCompleted": total_completed,
            "data": {
                "users": users_list,
                "products": products_list,
                "clients": clients_list,
            }
        })

    except Exception as e:
        print(f"Error getting completed tasks status: {str(e)}")
        return jsonify({
            "error": f"Failed to get completed tasks status: {str(e)}",
            "success": False
        }), 500


def get_tasks_by_date_range(data, decoded_token, db):
    """Get tasks within a specific date range.
    
    Args:
        data: Request data containing 'date' (YYYY-MM-DD) and 'days' (int)
        decoded_token: Decoded Firebase Auth token
        db: Firestore database instance
        
    Returns:
        JSON response with list of tasks
    """
    try:
        uid = decoded_token.get("uid")
        if not uid:
            return jsonify({
                "error": "User ID required",
                "success": False
            }), 400

        start_date_str = data.get("date")
        days = data.get("days")
        
        if not start_date_str:
            return jsonify({
                "error": "Start date is required",
                "success": False
            }), 400
            
        if days is None:
            return jsonify({
                "error": "Days count is required",
                "success": False
            }), 400

        # Parse start date
        try:
            # Handle possible ISO format
            if isinstance(start_date_str, str):
                if "T" in start_date_str:
                    start_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
                else:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            elif isinstance(start_date_str, datetime):
                start_date = start_date_str
            else:
                raise ValueError("Invalid date format")
                
            # Normalize to start of day
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            
        except Exception:
            return jsonify({
                "error": "Invalid date format. Use YYYY-MM-DD",
                "success": False
            }), 400
            
        try:
            days_count = int(days)
        except ValueError:
            return jsonify({
                "error": "Days must be an integer",
                "success": False
            }), 400
            
        # Calculate end date (inclusive). days_count counts the start day
        # itself, so a 7-day window runs start .. start+6 — adding the full
        # count and then extending to end-of-day would return one day too many.
        from datetime import timedelta
        end_date = (start_date + timedelta(days=days_count - 1)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
        
        # Query tasks where user is assigned to
        # We process targetDate filtering in memory to be safe against index requirements and data inconsistencies
        
        matching_tasks = []
        
        # Query only by assignedToId, filter by date range in memory to handle null/inconsistent data
        tasks_query = db.collection("tasks").where("reviewState", "!=", "deleted").stream()
        for doc in tasks_query:
            task = doc.to_dict()
            task["id"] = doc.id
            target_date_raw = task.get("targetDate")


            # Strict check for valid data
            if target_date_raw is None :
                continue
                
            if isinstance(target_date_raw, str) and not target_date_raw.strip():
                continue
                
            # Parse target date
            task_date = None
            try:
                if isinstance(target_date_raw, datetime):
                    task_date = target_date_raw
                elif isinstance(target_date_raw, str):
                    if "T" in target_date_raw:
                        task_date = datetime.fromisoformat(target_date_raw.replace("Z", "+00:00"))
                    else:
                        task_date = datetime.strptime(target_date_raw[:10], "%Y-%m-%d")
                elif isinstance(target_date_raw, int):
                    task_date = datetime.fromtimestamp(target_date_raw / 1000.0)
            except Exception:
                continue

            if not task_date:
                continue
                
            # Firestore hands back tz-aware UTC. The window bounds are naive
            # Iraq-local days, so convert into that zone before dropping the
            # offset — discarding it instead would read a task written at local
            # midnight (21:00 UTC the day before) as the previous day.
            if task_date.tzinfo and not start_date.tzinfo:
                task_date = task_date.astimezone(IRAQ_TIMEZONE).replace(tzinfo=None)
            
            # Check if date is within range
            if start_date <= task_date <= end_date:
                matching_tasks.append(task)
        
        # Sort by target date
        def get_sort_key(t):
             d = t.get("targetDate")
             # Helper to make sort key comparable
             if isinstance(d, str): return d
             if isinstance(d, datetime): return d.isoformat()
             return str(d)

        matching_tasks.sort(key=get_sort_key)
        
        return jsonify({
            "success": True,
            "data": matching_tasks,
            "count": len(matching_tasks),
            "dateRange": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            }
        })
        
    except Exception as e:
        print(f"Error getting tasks in range: {str(e)}")
        return jsonify({
            "error": f"Failed to get tasks: {str(e)}",
            "success": False
        }), 500




def get_tasks_paginated(data, db):
    """Retrieve tasks with pagination and custom filtering.
    
    To avoid complex index requirements, we fetch tasks by planId,
    and then filter, sort, and paginate them in memory.
    """
    try:
        plan_id = data.get("planId")
        if not plan_id:
            return jsonify({"error": "planId is required", "success": False}), 400

        task_type = data.get("taskType")
        page_size = data.get("pageSize", 20)
        last_document = data.get("lastDocument")  # offset index or taskId string
        target_date_filter = data.get("targetDateFilter", "all")
        
        # Filters
        filter_client_id = data.get("filterClientId")
        filter_product_id = data.get("filterProductId")
        filter_status = data.get("filterStatus")
        filter_priority = data.get("filterPriority")
        filter_city = data.get("filterCity")
        filter_city_client_ids = data.get("filterCityClientIds")

        # Resolve city in backend if filterCity is provided
        if filter_city:
            try:
                city_clients = db.collection("clients").where("city", "==", filter_city).stream()
                resolved_ids = [doc.id for doc in city_clients]
                if filter_city_client_ids is None:
                    filter_city_client_ids = resolved_ids
                else:
                    filter_city_client_ids = list(set(filter_city_client_ids).intersection(set(resolved_ids)))
            except Exception as e:
                print(f"Error resolving city clients: {e}")

        # Check if any user filter is active
        has_active_filter = bool(
            filter_client_id or
            filter_product_id or
            filter_status or
            filter_priority or
            filter_city or
            filter_city_client_ids is not None
        )

        # Query all tasks for the plan
        tasks_query = (
            db.collection("tasks")
            .where("planId", "==", plan_id)
            .stream()
        )
        
        all_tasks = []
        for doc in tasks_query:
            task = doc.to_dict()
            if task.get("reviewState") == "deleted":
                continue
            task["id"] = doc.id
            all_tasks.append(task)

        # Apply filters in memory
        filtered_tasks = []
        for task in all_tasks:
            # TaskType filter
            if task_type and task.get("taskType") != task_type:
                continue

            # TargetDate filter
            target_date = task.get("targetDate")
            # Ignore targetDateFilter if any user filter is active
            if not has_active_filter:
                if target_date_filter == "withDate" and target_date is None:
                    continue
                elif target_date_filter == "withoutDate" and target_date is not None:
                    continue

            # Client ID filter
            if filter_client_id and task.get("clientId") != filter_client_id:
                continue

            # Product ID filter
            if filter_product_id and task.get("productId") != filter_product_id:
                continue

            # Status filter
            if filter_status:
                task_status = task.get("status") or "pending"
                completed_aliases = {"completed", "مكتمل"}
                pending_aliases = {"pending", "قيد الانجاز", "قيد الإنجاز"}
                canceled_aliases = {"canceled", "cancelled", "ملغي"}
                reset_aliases = {"reset", "إعادة تعيين", "اعادة تعيين"}
                
                f_status = str(filter_status).strip().lower()
                t_status = str(task_status).strip().lower()
                
                is_match = False
                if f_status in completed_aliases:
                    is_match = t_status in completed_aliases
                elif f_status in pending_aliases:
                    is_match = t_status in pending_aliases
                elif f_status in canceled_aliases:
                    is_match = t_status in canceled_aliases
                elif f_status in reset_aliases:
                    is_match = t_status in reset_aliases
                else:
                    is_match = (t_status == f_status)
                
                if not is_match:
                    continue

            # Priority filter
            if filter_priority:
                task_priority = task.get("priority")
                high_aliases = {"a", "high"}
                medium_aliases = {"b", "medium"}
                low_aliases = {"c", "low"}
                
                f_pri = str(filter_priority).strip().lower()
                t_pri = str(task_priority or "").strip().lower()
                
                is_match = False
                if f_pri in high_aliases:
                    is_match = t_pri in high_aliases
                elif f_pri in medium_aliases:
                    is_match = t_pri in medium_aliases
                elif f_pri in low_aliases:
                    is_match = t_pri in low_aliases
                else:
                    is_match = (t_pri == f_pri)
                
                if not is_match:
                    continue

            # City filter (pre-resolved client IDs list)
            if filter_city_client_ids is not None:
                if not filter_city_client_ids:
                    # If empty client IDs list, no tasks can match
                    continue
                if task.get("clientId") not in filter_city_client_ids:
                    continue

            filtered_tasks.append(task)

        # Helper to parse targetDate / createdAt for sorting
        def parse_datetime(val):
            if not val:
                return datetime.min
            if isinstance(val, datetime):
                return val
            if isinstance(val, str):
                try:
                    return datetime.fromisoformat(val.replace("Z", "+00:00"))
                except Exception:
                    pass
            return datetime.min

        # Sort tasks descending by createdAt, and then by document id
        filtered_tasks.sort(
            key=lambda t: (parse_datetime(t.get("createdAt")), t.get("id", "")),
            reverse=True
        )

        # Paginate
        start_index = 0
        if last_document:
            try:
                start_index = int(last_document)
            except ValueError:
                # If last_document is a taskId string
                start_index = -1
                for idx, t in enumerate(filtered_tasks):
                    if t.get("id") == last_document:
                        start_index = idx + 1
                        break
                if start_index == -1:
                    start_index = 0

        paginated_tasks = filtered_tasks[start_index : start_index + page_size]
        has_more = (start_index + page_size) < len(filtered_tasks)
        
        next_last_document = None
        if paginated_tasks:
            next_last_document = paginated_tasks[-1].get("id")

        # Convert datetime objects to string representation for serialization
        def serialize_item(item):
            if isinstance(item, datetime):
                return item.isoformat()
            if isinstance(item, dict):
                return {k: serialize_item(v) for k, v in item.items()}
            if isinstance(item, list):
                return [serialize_item(v) for v in item]
            return item

        serialized_tasks = serialize_item(paginated_tasks)

        return jsonify({
            "success": True,
            "tasks": serialized_tasks,
            "hasMore": has_more,
            "lastDocument": next_last_document
        }), 200

    except Exception as e:
        print(f"Error in get_tasks_paginated: {str(e)}")
        traceback.print_exc()
        return jsonify({
            "error": f"Failed to get tasks: {str(e)}",
            "success": False
        }), 500


