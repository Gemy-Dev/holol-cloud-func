"""Products and clients data module."""
from flask import jsonify


def get_products(decoded_token, db):
    """Get all products with expanded relationships"""
    products_ref = db.collection("products")
    products = []
    
    for doc in products_ref.stream():
        product = doc.to_dict()
        product["id"] = doc.id
        product["imageUrl"] = product.get("imageUrl", "")

        # Expand manufacturer
        manufacturer_field = product.get("manufacturer")
        manufacturer_id = manufacturer_field.get("id") if isinstance(manufacturer_field, dict) else manufacturer_field
        if manufacturer_id:
            manufacturer_doc = db.collection("manufacturers").document(str(manufacturer_id)).get()
            product["manufacturer"] = manufacturer_doc.to_dict() if manufacturer_doc.exists else None
            if product["manufacturer"]:
                product["manufacturer"]["id"] = manufacturer_doc.id
        else:
            product["manufacturer"] = None

        # Expand procedures
        procedure_ids = product.get("procedures", [])
        procedures = []
        for pid in procedure_ids:
            proc_id = pid.get("id") if isinstance(pid, dict) else pid
            proc_doc = db.collection("procedures").document(str(proc_id)).get()
            if proc_doc.exists:
                proc = proc_doc.to_dict()
                proc["id"] = proc_doc.id
                procedures.append(proc)
        product["procedures"] = procedures

        # Expand marketing tasks
        marketing_taks = product.get("marketingTasks", [])
        product["marketingTasks"] = marketing_taks

        products.append(product)

    return jsonify(products)


def get_plan_products(plan_id, db):
    """Get products for a specific plan"""
    plan_doc = db.collection("plans").document(str(plan_id)).get()
    if not plan_doc.exists:
        return jsonify({"error": "Plan not found"}), 404

    plan_data = plan_doc.to_dict()
    products_ids = plan_data.get("productsIds", [])
    products = []

    for pid in products_ids:
        product_doc = db.collection("products").document(str(pid)).get()
        if not product_doc.exists:
            continue
        product = product_doc.to_dict()
        product["id"] = product_doc.id
        product["imageUrl"] = product.get("imageUrl", "")

        # Expand manufacturer
        manufacturer_field = product.get("manufacturer")
        manufacturer_id = manufacturer_field.get("id") if isinstance(manufacturer_field, dict) else manufacturer_field
        if manufacturer_id:
            manufacturer_doc = db.collection("manufacturers").document(str(manufacturer_id)).get()
            product["manufacturer"] = manufacturer_doc.to_dict() if manufacturer_doc.exists else None
            if product["manufacturer"]:
                product["manufacturer"]["id"] = manufacturer_doc.id
        else:
            product["manufacturer"] = None

        # Expand procedures
        procedure_ids = product.get("procedures", [])
        procedures = []
        for proc_id in procedure_ids:
            proc_id_val = proc_id.get("id") if isinstance(proc_id, dict) else proc_id
            proc_doc = db.collection("procedures").document(str(proc_id_val)).get()
            if proc_doc.exists:
                proc = proc_doc.to_dict()
                proc["id"] = proc_doc.id
                procedures.append(proc)
        product["procedures"] = procedures

        # Expand marketing tasks
        task_ids = product.get("marketingTasks", [])
        tasks = []
        for task_id in task_ids:
            task_id_val = task_id.get("id") if isinstance(task_id, dict) else task_id
            task_doc = db.collection("marketing_tasks").document(str(task_id_val)).get()
            if task_doc.exists:
                task = task_doc.to_dict()
                task["id"] = task_doc.id
                tasks.append(task)
        product["marketingTasks"] = tasks

        products.append(product)

    return jsonify(products)


def get_clients(decoded_token, db):
    """Get all clients with expanded relationships"""
    try:
        clients_ref = db.collection("clients")
        clients = []
        for doc in clients_ref.stream():
            try:
                client = doc.to_dict()
                if client is None:
                    continue
                    
                client["id"] = doc.id

                # Expand department
                department_id = client.get("department")
                if department_id:
                    try:
                        department_doc = db.collection("departments").document(str(department_id)).get()
                        client["department"] = department_doc.to_dict() if department_doc.exists else None
                        if client["department"]:
                            client["department"]["id"] = department_doc.id
                    except Exception as e:
                        print(f"Error expanding department {department_id}: {str(e)}")
                        client["department"] = None
                else:
                    client["department"] = None

                # Expand specialty
                specialty_id = client.get("specialty")
                if specialty_id:
                    try:
                        specialty_doc = db.collection("specialties").document(str(specialty_id)).get()
                        client["specialty"] = specialty_doc.to_dict() if specialty_doc.exists else None
                        if client["specialty"]:
                            client["specialty"]["id"] = specialty_doc.id
                    except Exception as e:
                        print(f"Error expanding specialty {specialty_id}: {str(e)}")
                        client["specialty"] = None
                else:
                    client["specialty"] = None

                # Handle client type
                client_type = client.get("clientType", "hospital")
                if client_type in ["hospital", "مستشفى", "مركز", "medicalCenter"]:
                    hospital_info = client.get("additionalInfo")
                    if hospital_info:
                        procedures_info = hospital_info.get("procedures", [])
                        expanded_procedures = []
                        for proc_info in procedures_info:
                            try:
                                proc_id = proc_info.get("procedure")
                                if proc_id:
                                    count = proc_info.get("count", 0)
                                    proc_doc = db.collection("procedures").document(str(proc_id)).get()
                                    procedure_data = proc_doc.to_dict() if proc_doc.exists else None
                                    if procedure_data:
                                        procedure_data["id"] = proc_doc.id
                                    expanded_procedures.append({
                                        "procedure": procedure_data,
                                        "count": count
                                    })
                            except Exception as e:
                                print(f"Error expanding procedure: {str(e)}")
                                continue
                        hospital_info["procedures"] = expanded_procedures
                        client["additionalInfo"] = hospital_info
                elif client_type in ["clinic", "عيادة"]:
                    hospital_info = client.get("additionalInfo")
                    client["additionalInfo"] = hospital_info
                else:
                    client["additionalInfo"] = None

                clients.append(client)
            except Exception as e:
                print(f"Error processing client document {doc.id}: {str(e)}")
                continue

        return jsonify(clients)
    except Exception as e:
        import traceback
        error_msg = f"Error in get_clients: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        return jsonify({"error": error_msg}), 500

