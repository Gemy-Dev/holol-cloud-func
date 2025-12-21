"""Task management module."""
from flask import jsonify
from firebase_admin import firestore
import traceback
from datetime import datetime


def _fetch_eligible_clients(department_ids, cities, db):
    """Fetch eligible clients based on departments and cities.
    
    Only returns clients with state "approved" (approved).
    Handles Firebase whereIn limitation (max 10 values) by batching.
    Returns detailed error information if no clients found.
    
    Args:
        department_ids: List of department IDs
        cities: List of city names
        db: Firestore database instance
        
    Returns:
        List of client dictionaries with id added (only approved clients)
        
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
        
        # Test individual queries for diagnostics (with approved state filter)
        approved_state = "approved"
        for dept_id in department_ids_list[:5]:
            try:
                dept_results = list(
                    db.collection("clients")
                    .where("department", "==", dept_id)
                    .where("state", "==", approved_state)
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
                    .where("state", "==", approved_state)
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
                    .where("state", "==", approved_state)
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
        approved_state = "approved"
        
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
                    
                    # Add state filter: only approved clients
                    base_query = base_query.where("state", "==", approved_state)
                    
                    # Get all clients matching departments and state, then filter by city
                    for doc in base_query.stream():
                        client = doc.to_dict()
                        client_city = str(client.get("city", "")).strip()
                        client_state = str(client.get("state", "")).strip()
                        
                        # Check if client's city matches any requested city and state is approved
                        if client_city in cities_set and client_state == approved_state:
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
                        
                        # Add state filter: only approved clients
                        base_query = base_query.where("state", "==", approved_state)
                        
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
            # Fallback: Check what cities actually exist for the requested departments (with approved state)
            approved_state = "approved"
            actual_cities_for_depts = set()
            try:
                for i in range(0, min(len(department_ids_list), batch_size)):
                    test_dept = department_ids_list[i]
                    test_query = (
                        db.collection("clients")
                        .where("department", "==", test_dept)
                        .where("state", "==", approved_state)
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
            error_parts.append(f"Required state: approved (approved)")
            
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
                error_parts.append("No approved clients (approved) found with requested departments (even without city filter)")
            
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
        List of influencer doctor dictionaries with name, phone, email
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
                "email": doctor.get("email", "")
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
    product_ids_set = set(product_ids)
    
    try:
        for doc in db.collection("products").stream():
            if doc.id in product_ids_set:
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
        doctor: Doctor dictionary with name, phone, email
        db: Firestore database instance
        
    Returns:
        True if task was created, False if it already exists
        
    Raises:
        Exception: If task creation fails
    """
    # Extract marketing task name for comparison
    if isinstance(marketing_task, dict):
        marketing_task_name = marketing_task.get("name") or marketing_task.get("id") or str(marketing_task)
        marketing_task_data = marketing_task
    else:
        marketing_task_name = str(marketing_task)
        marketing_task_data = marketing_task if marketing_task else {}
    
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
        
        # Get salesRepresentativeIds and salesManagerId from plan
        sales_representative_ids = plan_data.get("salesRepresentativeIds", [])
        sales_manager_id = plan_data.get("salesManagerId", "")
        
        # assignedToId - typically the first sales representative or empty
        assigned_to_id = sales_representative_ids[0] if sales_representative_ids else ""
        
        # Get priority from client (handle both enum name and value)
        client_priority = client.get("priority")
        if isinstance(client_priority, dict):
            priority_name = client_priority.get("name") or client_priority.get("value") or "medium"
        elif isinstance(client_priority, str):
            priority_name = client_priority
        else:
            priority_name = "medium"  # Default priority
        
        # Create new task matching Flutter TaskModel structure with doctor info
        task_data = {
            "taskType": "planned",  # TaskType.planned.value
            "salesRepresentativeIds": sales_representative_ids,
            "salesManagerId": sales_manager_id,
            "assignedToId": assigned_to_id,
            "planId": plan_id,
            "clientId": client["id"],
            "targetDate": None,  # Optional, can be set later
            "productId": product["id"],
            "status": "قيد الانجاز",  # Default status (TaskStatus enum)
            "cancelReason": None,  # Optional
            "state": "قيد المراجعة",  # Default review state (ReviewState enum)
            "visitResult": None,  # Optional
            "priority": priority_name,
            "note": None,  # Optional
            "doctorName": doctor.get("name", ""),  # Doctor name from influencer doctor
            "createdAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
            "updatedAt": firestore.SERVER_TIMESTAMP,  # type: ignore[attr-defined]
            "marketingTask": marketing_task_data,  # Keep for backward compatibility
        }
        
        task_ref = db.collection("tasks").document()
        task_ref.set(task_data)
        
        return True
        
    except Exception as e:
        raise Exception(f"Failed to create task for doctor {doctor.get('name')}, client {client.get('id')}, product {product.get('id')}: {str(e)}")


def create_plan_tasks(data, db):
    """Create tasks based on plan model.
    
    Extracts all data from the Plan model:
    - Product IDs from plan.targetProductsSales
    - Client criteria from plan.departmentsIds and plan.cities
    
    Matches the Dart implementation behavior:
    - Checks for existing tasks to avoid duplicates
    - Only creates tasks for approved clients (state = "approved")
    
    Args:
        data: Request data containing plan information
        db: Firestore database instance
        
    Returns:
        JSON response with success status and created tasks
    """
    # Extract and validate plan data
    plan_data = data.get("plan")
    if not plan_data:
        return jsonify({
            "error": "Plan data is required",
            "success": False
        }), 400
    
    plan_id = plan_data.get("id")
    if not plan_id or plan_id == "":
        return jsonify({
            "error": "Plan ID is required and must not be empty",
            "success": False
        }), 400
    
    # Extract product IDs from plan.targetProductsSales
    target_product_sales = plan_data.get("targetProductSales", [])
    if not target_product_sales:
        return jsonify({
            "error": "Plan has no target products",
            "success": False,
            "planId": plan_id
        }), 400
    
    product_ids = []
    for item in target_product_sales:
        if isinstance(item, dict):
            product_id = item.get("productId")
            if product_id:
                product_ids.append(product_id)
    
    if not product_ids:
        return jsonify({
            "error": "No effective product IDs found in targetProductSales",
            "success": False,
            "planId": plan_id
        }), 400
    
    # Extract client criteria from plan
    plan_cities = plan_data.get("cities", [])
    plan_departments = plan_data.get("departmentsIds", [])
    
    if not plan_departments:
        return jsonify({
            "error": "Plan has no departments",
            "success": False,
            "planId": plan_id
        }), 400
    
    if not plan_cities:
        return jsonify({
            "error": "Plan has no cities",
            "success": False,
            "planId": plan_id
        }), 400
    
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
            
            # Track clients without influencer doctors
            if not influencer_doctors:
                clients_without_doctors += 1
                continue
            
            total_influencer_doctors += len(influencer_doctors)
            
            # Loop through each influencer doctor
            for doctor in influencer_doctors:
                for product in products:
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
    client_state = client_data.get("state")
    
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
    
    # Only process approved clients
    if client_state != "approved":
        return jsonify({
            "success": True,
            "message": "Client is not approved yet. No tasks created.",
            "clientId": client_id,
            "clientState": client_state,
            "tasksCreated": 0
        })
    
    try:
        # Find matching plans where:
        # - client.city in plan.cities
        # - client.department in plan.departmentsIds
        matching_plans = []
        
        try:
            # Get all plans
            plans_query = db.collection("plans").stream()
            
            for plan_doc in plans_query:
                plan = plan_doc.to_dict()
                plan["id"] = plan_doc.id
                
                plan_cities = plan.get("cities", [])
                plan_departments = plan.get("departmentsIds", [])
                plan_clients_ids = plan.get("clientsIds", [])
                
                # Check if client's city and department match the plan
                # AND client is not already in the plan's clientsIds
                if (client_city in plan_cities and 
                    client_department in plan_departments and 
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
        
        if not influencer_doctors:
            return jsonify({
                "success": True,
                "message": "Client has no influencer doctors. No tasks created.",
                "clientId": client_id,
                "matchingPlans": len(matching_plans),
                "tasksCreated": 0
            })
        
        # Create tasks for each matching plan
        total_created = 0
        total_skipped = 0
        task_errors = []
        plans_processed = []
        
        for plan in matching_plans:
            plan_id = plan.get("id")
            if not plan_id:
                continue
            
            # Get product IDs from plan.targetProductSales
            target_product_sales = plan.get("targetProductSales", [])
            if not target_product_sales:
                continue
            
            product_ids = []
            for item in target_product_sales:
                if isinstance(item, dict):
                    product_id = item.get("productId")
                    if product_id:
                        product_ids.append(product_id)
            
            if not product_ids:
                continue
            
            # Fetch products and filter by client department
            try:
                eligible_products = []
                
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
                
                if not eligible_products:
                    continue
                
                # Create tasks for each influencer doctor, product, and marketing task
                plan_created = 0
                plan_skipped = 0
                
                for doctor in influencer_doctors:
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
                
                # Update plan to add client ID to clientsIds array
                try:
                    plan_ref = db.collection("plans").document(plan_id)
                    plan_ref.update({
                        "clientsIds": firestore.ArrayUnion([client_id])  # type: ignore[attr-defined]
                    })
                except Exception as update_error:
                    # Log error but don't fail the entire operation
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
            
            except Exception as plan_error:
                task_errors.append({
                    "planId": plan_id,
                    "error": f"Failed to process plan: {str(plan_error)}"
                })
                continue
        
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
       - Client's state is "approved" (approved)
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
        # - Client's department is in product's departmentsIds
        # - Client's city is in plan's cities
        # - Client's state is "approved" (approved)
        eligible_clients = []
        approved_state = "approved"
        
        try:
            # Query clients by department (handle Firebase whereIn limitation)
            department_batches = []
            batch_size = 10
            for i in range(0, len(product_departments), batch_size):
                batch = product_departments[i:i + batch_size]
                department_batches.append(batch)
            
            for dept_batch in department_batches:
                clients_query = (
                    db.collection("clients")
                    .where("department", "in", dept_batch)
                    .where("state", "==", approved_state)
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
            if not influencer_doctors:
                continue
            
            client_created = 0
            client_skipped = 0
            
            # Create tasks for each doctor, product, and marketing task combination
            for doctor in influencer_doctors:
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

        # Query tasks where user is in salesRepresentativeIds
        # We filter targetDate != None in memory to avoid complex composite index requirements
        # with array-contains queries.
        tasks_query = db.collection("tasks").where("salesRepresentativeIds", "array_contains", uid).stream()
        
        # Dictionary to store counts: date_str -> count
        stats_map = {}
        
        for doc in tasks_query:
            task = doc.to_dict()
            target_date = task.get("targetDate")
            
            if not target_date:
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
                except:
                    # Fallback or ignore invalid formats
                    continue
            elif isinstance(target_date, int):
                # Timestamp in milliseconds
                try:
                    date_obj = datetime.fromtimestamp(target_date / 1000.0)
                    date_key = date_obj.strftime("%Y-%m-%d")
                except:
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
            
        # Calculate end date (inclusive)
        from datetime import timedelta
        end_date = start_date + timedelta(days=days_count)
        # Set end date to end of day to be inclusive
        end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        
        # Query tasks where user is in salesRepresentativeIds
        # We process targetDate filtering in memory to be safe against index requirements and data inconsistencies
        
        matching_tasks = []
        
        tasks_query = db.collection("tasks").where("salesRepresentativeIds", "array_contains", uid).where("targetDate", ">=", start_date).where("targetDate", "<=", end_date).stream()
        for doc in tasks_query:
            task = doc.to_dict()
            task["id"] = doc.id
            target_date_raw = task.get("targetDate")
            
            # Strict check for valid data
            if target_date_raw is None:
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
            except:
                continue
                
            if not task_date:
                continue
                
            # Remove timezone info for comparison if needed
            if task_date.tzinfo and not start_date.tzinfo:
                 task_date = task_date.replace(tzinfo=None)
            
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

