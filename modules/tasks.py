"""Task management module."""
from flask import jsonify
from firebase_admin.firestore import SERVER_TIMESTAMP
import traceback
from datetime import datetime


def create_plan_tasks(data, db):
    """Create tasks based on plan model, products, and clients"""
    try:
        # Extract and validate plan data
        plan_data = data.get("plan")
        if not plan_data:
            return jsonify({"error": "Plan data is required", "success": False}), 400

        # Extract plan fields using sets for better performance
        plan_cities = set(plan_data.get("cities", []))
        plan_departments = set(plan_data.get("departmentsIds", []))
        
        # Update to use targetProductSales instead of productsIds
        target_product_sales = plan_data.get("targetProductSales", [])
        print(f"Raw targetProductSales: {target_product_sales}")
        
        # Handle the targetProductSales properly
        plan_products = set()
        target_sales_map = {}  # Map productId to targetSales
        
        for item in target_product_sales:
            if isinstance(item, dict):
                product_id = item.get("productId")
                target_sales = item.get("targetSales", 0)
                if product_id:
                    plan_products.add(product_id)
                    target_sales_map[product_id] = target_sales
        
        plan_delivery_id = plan_data.get("deliveryId")
        plan_id = plan_data.get("id")

        print(f"plan cities: {plan_cities}")
        print(f"plan departments: {plan_departments}")
        print(f"plan products: {plan_products}")
        print(f"target sales map: {target_sales_map}")

        # Validate required fields
        if not all([plan_cities, plan_departments, plan_delivery_id, plan_id]):
            return jsonify({
                "error": "Missing required plan fields",
                "success": False
            }), 400

        # Get only relevant products
        products = []
        if plan_products:
            products_ref = db.collection("products").stream()
            for doc in products_ref:
                product = doc.to_dict()
                product["id"] = doc.id
                if product["id"] in plan_products:
                    # Add target sales info to the product
                    product["targetSales"] = target_sales_map.get(product["id"], 0)
                    products.append(product)

        print(f"all products: {len(products)}")

        # Get relevant clients
        clients = []
        clients_ref = db.collection("clients").stream()
        for doc in clients_ref:
            client = doc.to_dict()
            client["id"] = doc.id

            client_city = client.get("city")
            client_department_id = client.get("department")

            if (client_city and client_department_id and
                    client_city in plan_cities and
                    client_department_id in plan_departments):
                clients.append(client)

        print(f"all clients: {len(clients)}")

        # Create tasks using batch operations
        batch = db.batch()
        created_tasks = []
        batch_count = 0

        for client in clients:
            for product in products:
                marketing_tasks = product.get("marketingTasks", [])
                for marketing_task in marketing_tasks:
                    task_ref = db.collection("tasks").document()
                    task_data = {
                        "deliveryId": plan_delivery_id,
                        "productId": product["id"],
                        "planId": plan_id,
                        "clientId": client["id"],
                        "targetDate": None,
                        "priority": client.get("priority"),
                        "status": "قيد الانجاز",
                        "state": "قيد المراجعة",
                        "marketingTask": marketing_task,
                        "targetSales": product.get("targetSales", 0),  # Add target sales to task
                        "createdAt": SERVER_TIMESTAMP,
                        "updatedAt": None
                    }

                    batch.set(task_ref, task_data)
                    task_data["id"] = task_ref.id
                    created_tasks.append(task_data)
                    batch_count += 1

                    # Commit batch every 500 operations
                    if batch_count % 500 == 0:
                        batch.commit()
                        batch = db.batch()

        # Commit remaining operations
        if batch_count % 500 != 0:
            batch.commit()

        return jsonify({
            "success": True,
            "tasksCreated": len(created_tasks),
            "tasks": created_tasks
        })

    except Exception as e:
        return jsonify({
            "error": str(e),
            "success": False,
            "traceback": traceback.format_exc()
        }), 500

