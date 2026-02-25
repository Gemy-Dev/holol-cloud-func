"""Opportunities management module."""
from flask import jsonify
import traceback


def get_opportunity_stats(db):
    """Get opportunity statistics grouped by mainId.

    Loops over all opportunities and aggregates the count of
    completed and pending taskStatus for each mainId.

    Args:
        db: Firestore database instance

    Returns:
        JSON response with list of stats:
        [{ mainId: 'xxx', completed: N, pending: N }, ...]
    """
    try:
        opportunities = db.collection("tasks").where("taskType", "==", "opportunity").stream()

        stats_map = {}

        for doc in opportunities:
            opp = doc.to_dict()
            main_id = opp.get("mainOpportunityId")
            task_status = opp.get("status")

            if not main_id:
                continue

            if main_id not in stats_map:
                stats_map[main_id] = {"mainId": main_id, "completed": 0, "pending": 0}

            if task_status in ("completed", "مكتمل"):
                stats_map[main_id]["completed"] += 1
            elif task_status in ("pending", "قيد الانجاز"):
                stats_map[main_id]["pending"] += 1

        result = list(stats_map.values())

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        print(f"Error getting opportunity stats: {str(e)}")
        print(traceback.format_exc())
        return jsonify({
            "error": f"Failed to get opportunity stats: {str(e)}",
            "success": False
        }), 500
