"""Task management module."""
from flask import jsonify
from firebase_admin.firestore import SERVER_TIMESTAMP
import traceback
from datetime import datetime


def _fetch_target_products(product_ids, department_ids, db):
    """Fetch products based on departments from the plan.
    
    Loops through plan departments and gets products whose departmentsIds 
    contain each department, then filters to only include products in the 
    target product sales list.
    
    Args:
        product_ids: List of product IDs from targetProductSales
        department_ids: List of department IDs from the plan
        db: Firestore database instance
        
    Returns:
        List of product dictionaries with id added
        
    Raises:
        Exception: If the query fails
    """
    try:
        all_products = []
        product_ids_set = set(product_ids)
        seen_product_ids = set()  # Track seen products to avoid duplicates
        
        print(f"Looking for products with IDs: {product_ids}")
        print(f"Filtering by departments: {department_ids}")
        
        # Loop through each department in plan departmentsIds
        for department_id in department_ids:
            print(f"Querying products for department: {department_id}")
            try:
                # Query products where departmentsIds array contains this department
                query = db.collection("products").where("departmentsIds", "array_contains", department_id)
                snapshot = query.stream()
                
                dept_product_count = 0
                for doc in snapshot:
                    product_id = doc.id
                    dept_product_count += 1
                    product = doc.to_dict()
                    product_departments = product.get('departmentsIds', [])
                    print(f"Found product {product_id} in department {department_id}, has departmentsIds: {product_departments}")
                    
                    # Only include products that are in the target product sales list
                    # and haven't been added yet
                    if product_id in product_ids_set:
                        if product_id not in seen_product_ids:
                            product["id"] = product_id
                            all_products.append(product)
                            seen_product_ids.add(product_id)
                            print(f"Added product {product_id} to results")
                        else:
                            print(f"Product {product_id} already added (duplicate)")
                    else:
                        print(f"Product {product_id} not in target product IDs list (expected: {product_ids_set})")
                
                print(f"Found {dept_product_count} products for department {department_id}")
            except Exception as dept_error:
                print(f"Error querying products for department {department_id}: {dept_error}")
                # Continue with next department
                continue
        
        print(f"Total products after filtering: {len(all_products)}")
        return all_products
    except Exception as e:
        print(f"Error fetching products: {e}")
        print(traceback.format_exc())
        raise Exception(f"Failed to fetch target products: {str(e)}")


def _fetch_eligible_clients(department_ids, cities, db):
    """Fetch eligible clients based on departments and cities.
    
    Handles Firebase whereIn limitation (max 10 values) by batching.
    
    Args:
        department_ids: List of department IDs
        cities: List of city names
        db: Firestore database instance
        
    Returns:
        List of client dictionaries with id added
        
    Raises:
        Exception: If the query fails
    """
    try:
        all_clients = []
        department_ids_list = list(department_ids) if isinstance(department_ids, set) else department_ids
        cities_list = list(cities) if isinstance(cities, set) else cities
        
        print(f"Looking for clients with departments: {department_ids_list}")
        print(f"Looking for clients with cities: {cities_list}")
        
        # Handle Firebase whereIn limitation (max 10 values) by batching departments
        batch_size = 10
        
        # First, let's try a test query to see what state values exist and sample data
        test_query = db.collection("clients").limit(10).stream()
        test_states = set()
        sample_departments = set()
        sample_cities = set()
        for doc in test_query:
            client_data = doc.to_dict()
            state = client_data.get('state')
            if state:
                test_states.add(state)
            dept = client_data.get('department')
            if dept:
                sample_departments.add(str(dept))
            city = client_data.get('city')
            if city:
                sample_cities.add(str(city))
        print(f"Sample client states found in database: {test_states}")
        print(f"Sample departments found: {list(sample_departments)[:5]}")
        print(f"Sample cities found: {list(sample_cities)[:5]}")
        
        # Try querying without state filter first to see if any clients match departments/cities
        print("\n--- Testing query without state filter ---")
        test_count = 0
        for i in range(0, min(len(department_ids_list), batch_size)):
            test_dept = department_ids_list[i]
            try:
                test_query_no_state = db.collection("clients").where("department", "==", test_dept).limit(5).stream()
                for doc in test_query_no_state:
                    client_data = doc.to_dict()
                    test_count += 1
                    print(f"Test client found: id={doc.id}, department={client_data.get('department')}, city={client_data.get('city')}, state={client_data.get('state')}")
                    if test_count >= 3:
                        break
            except Exception as e:
                print(f"Test query error for department {test_dept}: {e}")
        print(f"--- Found {test_count} test clients (without state filter) ---\n")
        
        for i in range(0, len(department_ids_list), batch_size):
            batch_departments = department_ids_list[i:i + batch_size]
            
            # Handle cities batching if needed (also max 10)
            for j in range(0, len(cities_list), batch_size):
                batch_cities = cities_list[j:j + batch_size]
                
                print(f"Querying clients: departments={batch_departments}, cities={batch_cities}")
                
                try:
                    # Build base query
                    base_query = db.collection("clients")
                    
                    # Apply department filter
                    if len(batch_departments) == 1:
                        base_query = base_query.where("department", "==", batch_departments[0])
                    else:
                        base_query = base_query.where("department", "in", batch_departments)
                    
                    # Apply city filter
                    if len(batch_cities) == 1:
                        base_query = base_query.where("city", "==", batch_cities[0])
                    else:
                        base_query = base_query.where("city", "in", batch_cities)
                    
                    # Execute query (no state filter - include all clients)
                    print(f"Querying clients with {len(batch_departments)} departments and {len(batch_cities)} cities")
                    snapshot = base_query.stream()
                    
                    batch_client_count = 0
                    for doc in snapshot:
                        client = doc.to_dict()
                        client["id"] = doc.id
                        all_clients.append(client)
                        batch_client_count += 1
                        print(f"Found eligible client: {client['id']}, department={client.get('department')}, city={client.get('city')}, state={client.get('state')}")
                    
                    print(f"Found {batch_client_count} clients in this batch")
                except Exception as query_error:
                    print(f"Error executing client query for departments={batch_departments}, cities={batch_cities}: {query_error}")
                    print(traceback.format_exc())
                    # Continue with next batch
                    continue
        
        print(f"Total eligible clients: {len(all_clients)}")
        return all_clients
    except Exception as e:
        print(f"Error fetching clients: {e}")
        print(traceback.format_exc())
        raise Exception(f"Failed to fetch eligible clients: {str(e)}")


def create_plan_tasks(data, db):
    """Create tasks based on plan model, products, and clients.
    
    This includes creating tasks for each eligible client and product combination.
    
    Args:
        data: Request data containing plan information
        db: Firestore database instance
        
    Returns:
        JSON response with success status and created tasks
    """
    try:
        # Extract and validate plan data
        plan_data = data.get("plan")
        if not plan_data:
            return jsonify({"error": "Plan data is required", "success": False}), 400

        # Extract plan fields
        plan_cities = plan_data.get("cities", [])
        plan_departments = plan_data.get("departmentsIds", [])
        
        # Validate plan data
        if not plan_departments:
            return jsonify({
                "error": "Plan has no departments",
                "success": False
            }), 400
        
        # Handle targetProductSales
        target_product_sales = plan_data.get("targetProductSales", [])
        if not target_product_sales:
            return jsonify({
                "error": "Plan has no target products",
                "success": False
            }), 400
        
        # Extract product IDs and create target sales map
        product_ids = []
        target_sales_map = {}  # Map productId to targetSales
        
        for item in target_product_sales:
            if isinstance(item, dict):
                product_id = item.get("productId")
                target_sales = item.get("targetSales", 0)
                if product_id:
                    product_ids.append(product_id)
                    target_sales_map[product_id] = target_sales
        
        if not product_ids:
            return jsonify({
                "error": "Plan has no valid target products",
                "success": False
            }), 400
        
        plan_delivery_id = plan_data.get("deliveryId")
        plan_id = plan_data.get("id")
        
        # Validate required fields
        if not all([plan_cities, plan_delivery_id, plan_id]):
            return jsonify({
                "error": "Missing required plan fields (cities, deliveryId, or id)",
                "success": False
            }), 400

        print(f"Plan cities: {plan_cities}")
        print(f"Plan departments: {plan_departments}")
        print(f"Product IDs: {product_ids}")
        print(f"Target sales map: {target_sales_map}")

        # Fetch products and clients (similar to Dart's Future.wait approach)
        products = _fetch_target_products(product_ids, plan_departments, db)
        
        # Add target sales info to products
        for product in products:
            product["targetSales"] = target_sales_map.get(product["id"], 0)
        
        print(f"Fetched products: {len(products)}")
        
        # Log product details
        for product in products:
            print(f"Product {product['id']} has {len(product.get('marketingTasks', []))} marketing tasks")

        # Fetch eligible clients with queries and approval filtering
        clients = _fetch_eligible_clients(plan_departments, plan_cities, db)

        print(f"Fetched eligible clients: {len(clients)}")
        print(f"Creating tasks for {len(clients)} clients and {len(products)} products")
        
        if len(products) == 0:
            return jsonify({
                "success": True,
                "tasksCreated": 0,
                "tasks": [],
                "debug": {
                    "message": "No products found matching the criteria",
                    "product_ids_requested": product_ids,
                    "departments_searched": plan_departments,
                    "products_found": len(products)
                }
            })
        
        if len(clients) == 0:
            return jsonify({
                "success": True,
                "tasksCreated": 0,
                "tasks": [],
                "debug": {
                    "message": "No eligible clients found matching the criteria",
                    "departments_searched": plan_departments,
                    "cities_searched": plan_cities,
                    "clients_found": len(clients)
                }
            })

        # Create tasks using batch operations
        batch = db.batch()
        created_tasks = []
        batch_count = 0

        try:
            for client in clients:
                for product in products:
                    marketing_tasks = product.get("marketingTasks", [])
                    if not marketing_tasks:
                        print(f"Warning: Product {product['id']} has no marketing tasks")
                        continue
                    
                    for marketing_task in marketing_tasks:
                        try:
                            task_ref = db.collection("tasks").document()
                            # Handle priority - in Flutter it's stored as priority.name (e.g., "high", "medium", "low")
                            client_priority = client.get("priority")
                            
                            # Ensure marketing_task is a dict/object that can be serialized
                            # If it's already a dict, use it directly; otherwise convert
                            if isinstance(marketing_task, dict):
                                marketing_task_data = marketing_task
                            else:
                                # If it's a string or other type, wrap it appropriately
                                marketing_task_data = marketing_task if marketing_task else {}
                            
                            task_data = {
                                "deliveryId": plan_delivery_id,
                                "productId": product["id"],
                                "planId": plan_id,
                                "clientId": client["id"],
                                "targetDate": None,
                                "priority": client_priority,  # This is the priority.name value from Flutter
                                "status": "قيد الانجاز",
                                "state": "قيد المراجعة",
                                "marketingTask": marketing_task_data,
                                "targetSales": product.get("targetSales", 0),
                                "createdAt": SERVER_TIMESTAMP,
                                "updatedAt": None
                            }

                            batch.set(task_ref, task_data)
                            task_data["id"] = task_ref.id
                            created_tasks.append(task_data)
                            batch_count += 1

                            # Commit batch every 500 operations (Firestore limit)
                            if batch_count % 500 == 0:
                                print(f"Committing batch of {batch_count} tasks...")
                                batch.commit()
                                print(f"Batch committed successfully")
                                batch = db.batch()
                        except Exception as task_error:
                            print(f"Error creating task for client {client.get('id')}, product {product.get('id')}: {task_error}")
                            print(traceback.format_exc())
                            # Continue with next task
                            continue

            # Commit remaining operations
            if batch_count % 500 != 0 and batch_count > 0:
                print(f"Committing final batch of {batch_count % 500} tasks...")
                batch.commit()
                print(f"Final batch committed successfully")
        except Exception as batch_error:
            print(f"Error during batch operations: {batch_error}")
            print(traceback.format_exc())
            # Try to commit what we have
            try:
                if batch_count > 0:
                    print("Attempting to commit partial batch...")
                    batch.commit()
            except Exception as commit_error:
                print(f"Error committing partial batch: {commit_error}")
            raise Exception(f"Failed to create tasks: {str(batch_error)}")

        print(f"Successfully created all tasks for plan {plan_id}")

        # Limit tasks in response to avoid large payloads
        # Return only first 100 task IDs to avoid response size issues
        tasks_to_return = created_tasks[:100] if len(created_tasks) > 100 else created_tasks
        
        # Clean up task data for response (remove complex objects if needed)
        cleaned_tasks = []
        for task in tasks_to_return:
            cleaned_task = {
                "id": task.get("id"),
                "planId": task.get("planId"),
                "clientId": task.get("clientId"),
                "productId": task.get("productId"),
                "deliveryId": task.get("deliveryId"),
            }
            cleaned_tasks.append(cleaned_task)

        return jsonify({
            "success": True,
            "tasksCreated": len(created_tasks),
            "tasks": cleaned_tasks,  # Return limited/simplified tasks
            "tasksCount": len(created_tasks),
            "debug": {
                "products_found": len(products),
                "clients_found": len(clients),
                "tasks_created": len(created_tasks),
                "tasks_returned_in_response": len(cleaned_tasks)
            }
        })

    except Exception as e:
        error_msg = str(e)
        print(f"Failed to create plan tasks: {error_msg}")
        return jsonify({
            "error": error_msg,
            "success": False,
            "traceback": traceback.format_exc()
        }), 500

