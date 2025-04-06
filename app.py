from flask import Flask, jsonify, request, Response
from datetime import datetime, timedelta
import json, os, csv, io

app = Flask(__name__)

# -----------------------
# Global Utility Functions
# -----------------------

def load_json(filename, default):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except Exception:
            return default
    else:
        return default

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

# -----------------------
# Logging Helpers (Persist to logs.json)
# -----------------------

LOGS = []  # In-memory log list

def append_log_to_file(log_entry):
    logs_data = load_json("logs.json", {"logs": []})
    logs_data["logs"].append(log_entry)
    save_json("logs.json", logs_data)

def record_log(user_id, action_type, item_id, from_container, to_container, reason):
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "userId": user_id,
        "actionType": action_type,
        "itemId": item_id,
        "details": {
            "fromContainer": from_container,
            "toContainer": to_container,
            "reason": reason
        }
    }
    LOGS.append(log_entry)
    append_log_to_file(log_entry)

# -----------------------
# Placement Algorithm Helper Functions
# -----------------------

def get_best_orientation(item, container_depth):
    w = item["width"]
    h = item["height"]
    d = item["depth"]
    orientations = [
        {"w": w, "h": h, "d": d, "area": w * h},
        {"w": w, "h": d, "d": h, "area": w * d},
        {"w": h, "h": d, "d": w, "area": h * d}
    ]
    fitting = [o for o in orientations if o["d"] <= container_depth]
    if not fitting:
        return None
    best = min(fitting, key=lambda o: o["area"])
    return (best["w"], best["h"], best["d"])

def split_free_rectangle(fr, pr):
    ix = max(fr["x"], pr["x"])
    iy = max(fr["y"], pr["y"])
    i_right = min(fr["x"] + fr["w"], pr["x"] + pr["w"])
    i_top = min(fr["y"] + fr["h"], pr["y"] + pr["h"])
    if i_right <= ix or i_top <= iy:
        return [fr]
    rects = []
    if ix > fr["x"]:
        rects.append({"x": fr["x"], "y": fr["y"], "w": ix - fr["x"], "h": fr["h"]})
    if i_right < fr["x"] + fr["w"]:
        rects.append({"x": i_right, "y": fr["y"], "w": (fr["x"] + fr["w"]) - i_right, "h": fr["h"]})
    if i_top < fr["y"] + fr["h"]:
        rects.append({"x": fr["x"], "y": i_top, "w": fr["w"], "h": (fr["y"] + fr["h"]) - i_top})
    if iy > fr["y"]:
        rects.append({"x": fr["x"], "y": fr["y"], "w": fr["w"], "h": iy - fr["y"]})
    return [r for r in rects if r["w"] > 0 and r["h"] > 0]

def prune_free_rectangles(free_rects):
    pruned = []
    for i, r1 in enumerate(free_rects):
        contained = False
        for j, r2 in enumerate(free_rects):
            if i != j:
                if (r1["x"] >= r2["x"] and r1["y"] >= r2["y"] and
                    r1["x"] + r1["w"] <= r2["x"] + r2["w"] and
                    r1["y"] + r1["h"] <= r2["y"] + r2["h"]):
                    contained = True
                    break
        if not contained:
            pruned.append(r1)
    return pruned

def find_position_for_rect(free_rects, rw, rh):
    best_score = None
    best_rect = None
    best_x = best_y = None
    for fr in free_rects:
        if fr["w"] >= rw and fr["h"] >= rh:
            leftover = fr["w"] * fr["h"] - rw * rh
            if best_score is None or leftover < best_score:
                best_score = leftover
                best_rect = fr
                best_x, best_y = fr["x"], fr["y"]
    return best_rect, best_x, best_y, best_score

def pack_open_face_in_container(container, items):
    W = container["width"]
    H = container["height"]
    D = container["depth"]
    free_rects = [{"x": 0.0, "y": 0.0, "w": W, "h": H}]
    placements = []
    unplaced = []
    for item in items:
        orient = get_best_orientation(item, D)
        if orient is None:
            unplaced.append(item)
            continue
        rw, rh, rd = orient
        fr, px, py, score = find_position_for_rect(free_rects, rw, rh)
        if fr is None:
            unplaced.append(item)
            continue
        placement = {
            "containerId": container["containerId"],
            "itemId": item["itemId"],
            "position": {
                "startCoordinates": {"width": px, "depth": 0.0, "height": py},
                "endCoordinates": {"width": px + rw, "depth": rd, "height": py + rh}
            }
        }
        placements.append(placement)
        placed_rect = {"x": px, "y": py, "w": rw, "h": rh}
        new_free = []
        for r in free_rects:
            new_free.extend(split_free_rectangle(r, placed_rect))
        free_rects = prune_free_rectangles(new_free)
    return placements, unplaced

def place_items_optimally(data):
    active_items = data.get("items", [])
    zone_to_items = {}
    for it in active_items:
        z = it["preferredZone"]
        zone_to_items.setdefault(z, []).append(it)
    all_open_face_placements = []
    unplaced_global = []
    for zone, items_list in zone_to_items.items():
        items_list.sort(key=lambda x: x.get("priority", 0), reverse=True)
        c_list = [c for c in data.get("containers", []) if c["zone"] == zone]
        c_list.sort(key=lambda c: c["containerId"])
        container_face_data = {
            c["containerId"]: {
                "container": c,
                "free_rects": [{"x": 0.0, "y": 0.0, "w": c["width"], "h": c["height"]}],
                "placements": []
            }
            for c in c_list
        }
        remaining = []
        for item in items_list:
            placed = False
            for cid, d_item in container_face_data.items():
                cont = d_item["container"]
                orient = get_best_orientation(item, cont["depth"])
                if orient is None:
                    continue
                rw, rh, rd = orient
                fr, px, py, score = find_position_for_rect(d_item["free_rects"], rw, rh)
                if fr is not None:
                    pdoc = {
                        "containerId": cid,
                        "itemId": item["itemId"],
                        "position": {
                            "startCoordinates": {"width": px, "depth": 0.0, "height": py},
                            "endCoordinates": {"width": px + rw, "depth": rd, "height": py + rh}
                        }
                    }
                    d_item["placements"].append(pdoc)
                    record_log("system", "NEW_PLACEMENT", item["itemId"], "NONE", cid, "Placed on open face.")
                    placed_rect = {"x": px, "y": py, "w": rw, "h": rh}
                    new_free = []
                    for r in d_item["free_rects"]:
                        new_free.extend(split_free_rectangle(r, placed_rect))
                    d_item["free_rects"] = prune_free_rectangles(new_free)
                    placed = True
                    break
            if not placed:
                remaining.append(item)
        for cid, d_item in container_face_data.items():
            all_open_face_placements.extend(d_item["placements"])
        unplaced_global.extend(remaining)
    rearrangements = []  # Additional rearrangement logic can be implemented here.
    return all_open_face_placements, rearrangements

def generate_rearrangements(old_placements, new_placements):
    rearrangements = []
    old_dict = {p["itemId"]: p for p in old_placements}
    new_dict = {p["itemId"]: p for p in new_placements}
    step = 1
    for itemId, newp in new_dict.items():
        if itemId in old_dict:
            oldp = old_dict[itemId]
            if (oldp["containerId"] != newp["containerId"] or oldp["position"] != newp["position"]):
                rearrangements.append({
                    "step": step,
                    "action": "move",
                    "itemId": itemId,
                    "fromContainer": oldp["containerId"],
                    "fromPosition": oldp["position"],
                    "toContainer": newp["containerId"],
                    "toPosition": newp["position"]
                })
                step += 1
        else:
            rearrangements.append({
                "step": step,
                "action": "place",
                "itemId": itemId,
                "fromContainer": "NONE",
                "fromPosition": {
                    "startCoordinates": {"width": 0, "depth": 0, "height": 0},
                    "endCoordinates": {"width": 0, "depth": 0, "height": 0}
                },
                "toContainer": newp["containerId"],
                "toPosition": newp["position"]
            })
            step += 1
    for itemId, oldp in old_dict.items():
        if itemId not in new_dict:
            rearrangements.append({
                "step": step,
                "action": "remove",
                "itemId": itemId,
                "fromContainer": oldp["containerId"],
                "fromPosition": oldp["position"],
                "toContainer": "NONE",
                "toPosition": {
                    "startCoordinates": {"width": 0, "depth": 0, "height": 0},
                    "endCoordinates": {"width": 0, "depth": 0, "height": 0}
                }
            })
            step += 1
    return rearrangements

# -----------------------
# API Endpoints Continued
# -----------------------

# 5. Retrieve Item (/api/retrieve)
@app.route("/api/retrieve", methods=["POST"])
def retrieve_item():
    data = request.get_json()
    if not data or "itemId" not in data or "userId" not in data or "timestamp" not in data:
        return jsonify({"success": False, "message": "Missing required field(s)"}), 400
    item_id = data["itemId"]
    user_id = data["userId"]
    timestamp = data["timestamp"]
    try:
        with open("placement.json", "r") as f:
            placement_data = json.load(f)
            placements = placement_data.get("placements", [])
    except Exception:
        return jsonify({"success": False, "message": "Placement file not found"}), 500
    new_placements = [p for p in placements if p.get("itemId") != item_id]
    if len(new_placements) == len(placements):
        return jsonify({"success": False, "message": "Item not found"}), 404
    placement_data["placements"] = new_placements
    try:
        with open("placement.json", "w") as f:
            json.dump(placement_data, f, indent=2)
    except Exception:
        return jsonify({"success": False, "message": "Error updating placement file"}), 500
    try:
        with open("items.json", "r") as f:
            items_data = json.load(f)
    except Exception:
        return jsonify({"success": True, "warning": "Item retrieved but failed to update usageLimit due to items file error"}), 200
    items = items_data.get("items", [])
    for item in items:
        if item.get("itemId") == item_id and item.get("usageLimit", 0) > 0:
            item["usageLimit"] -= 1
            break
    try:
        with open("items.json", "w") as f:
            json.dump(items_data, f, indent=2)
    except Exception:
        return jsonify({"success": True, "warning": "Item retrieved but failed to update items file"}), 200
    record_log(user_id, "ITEM_RETRIEVAL", item_id, "Placed", "Retrieved", f"Item retrieved at {timestamp}")
    return jsonify({"success": True})

# 6. Search Item (/api/search) - GET with query parameters
@app.route("/api/search", methods=["GET"])
def search_item():
    item_id = request.args.get("itemId")
    item_name = request.args.get("itemName")
    user_id = request.args.get("userId")
    if not item_id and not item_name:
        return jsonify({"success": False, "found": False, "message": "Missing itemId or itemName"}), 400
    search_identifier = item_id if item_id else item_name
    try:
        with open("placement.json", "r") as f:
            placement_data = json.load(f)
            placements = placement_data.get("placements", [])
    except Exception:
        return jsonify({"success": False, "found": False, "message": "Placement file not found"}), 500
    target = None
    for p in placements:
        if p.get("itemId") == search_identifier or p.get("name") == search_identifier:
            target = p
            break
    if target is None:
        record_log("system", "SEARCH_ITEM", search_identifier, "N/A", "N/A", "Item not found during search")
        return jsonify({"success": True, "found": False, "item": {}, "retrievalSteps": []})
    found_name = target.get("name") or target.get("itemName") or "Unknown"
    target_item = {
        "itemId": target["itemId"],
        "name": found_name,
        "containerId": target["containerId"],
        "zone": target["containerId"][:2] if len(target["containerId"]) >= 2 else "Unknown",
        "position": target["position"]
    }
    retrieval_steps = []
    target_start = target["position"]["startCoordinates"]
    if target_start.get("depth", 0) == 0:
        retrieval_steps.append({
            "step": 0,
            "action": "retrieve",
            "placeBack": False,
            "itemId": target["itemId"],
            "itemName": found_name
        })
    else:
        blocking_items = []
        t_x = target["position"]["startCoordinates"]["width"]
        t_y = target["position"]["startCoordinates"]["height"]
        t_w = target["position"]["endCoordinates"]["width"] - t_x
        t_h = target["position"]["endCoordinates"]["height"] - t_y
        for p in placements:
            if p["containerId"] == target["containerId"] and p["itemId"] != target["itemId"]:
                p_start = p["position"]["startCoordinates"]
                if p_start.get("depth", 0) == 0:
                    p_x = p_start["width"]
                    p_y = p_start["height"]
                    p_w = p["position"]["endCoordinates"]["width"] - p_x
                    p_h = p["position"]["endCoordinates"]["height"] - p_y
                    if (p_x < t_x + t_w and p_x + p_w > t_x and
                        p_y < t_y + t_h and p_y + p_h > t_y):
                        blocking_items.append(p)
        step = 1
        for block in blocking_items:
            block_name = block.get("name") or block.get("itemName") or "Unknown"
            retrieval_steps.append({
                "step": step,
                "action": "remove",
                "placeBack": True,
                "itemId": block["itemId"],
                "itemName": block_name
            })
            step += 1
        retrieval_steps.append({
            "step": step,
            "action": "retrieve",
            "placeBack": False,
            "itemId": target["itemId"],
            "itemName": found_name
        })
    record_log("system", "SEARCH_ITEM", search_identifier, "N/A", "N/A", "Item found during search")
    return jsonify({"success": True, "found": True, "item": target_item, "retrievalSteps": retrieval_steps})

# 7. Waste Management - Identify Waste Items (/api/waste/identify)
@app.route("/api/waste/identify", methods=["GET"])
def identify_waste():
    items_data = load_json("items.json", {"items": []})
    if isinstance(items_data, list):
        items = items_data
    else:
        items = items_data.get("items", [])
    current_date = datetime.utcnow()
    waste_items = []
    for item in items:
        reason = ""
        expiry_str = item.get("expiryDate")
        if expiry_str:
            try:
                expiry_date = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            except Exception:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ")
            if expiry_date < current_date:
                reason = "Expired"
        if not reason and item.get("usageLimit", 1) == 0:
            reason = "Out of Uses"
        if reason:
            waste_item = {
                "itemId": item.get("itemId", ""),
                "name": item.get("name", "Unknown"),
                "reason": reason,
                "containerId": item.get("containerId", "Not Placed"),
                "position": item.get("position", {
                    "startCoordinates": {"width": 0, "depth": 0, "height": 0},
                    "endCoordinates": {"width": 0, "depth": 0, "height": 0}
                })
            }
            waste_items.append(waste_item)
    record_log("system", "IDENTIFY_WASTE", "", "N/A", "N/A", "Waste identification completed")
    return jsonify({"success": True, "wasteItems": waste_items})

# 8. Waste Management - Return Plan (/api/waste/return-plan) [POST]
@app.route("/api/waste/return-plan", methods=["POST"])
def waste_return_plan():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Missing JSON body"}), 400
    undocking_container_id = data.get("undockingContainerId")
    undocking_date = data.get("undockingDate")
    max_weight = data.get("maxWeight")
    if not undocking_container_id or not undocking_date or max_weight is None:
        return jsonify({"success": False, "message": "Missing parameter(s)"}), 400
    try:
        max_weight = float(max_weight)
    except ValueError:
        return jsonify({"success": False, "message": "Invalid maxWeight"}), 400
    items_data = load_json("items.json", {"items": []})
    if isinstance(items_data, list):
        items = items_data
    else:
        items = items_data.get("items", [])
    current_date = datetime.utcnow()
    waste_items = []
    for item in items:
        reason = ""
        expiry_str = item.get("expiryDate")
        if expiry_str:
            try:
                expiry_date = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            except Exception:
                expiry_date = datetime.strptime(expiry_str, "%Y-%m-%dT%H:%M:%SZ")
            if expiry_date < current_date:
                reason = "Expired"
        if not reason and item.get("usageLimit", 1) == 0:
            reason = "Out of Uses"
        if reason:
            volume = item.get("width", 0) * item.get("height", 0) * item.get("depth", 0)
            weight = volume  # Assume weight equals volume.
            item["calculatedVolume"] = volume
            item["calculatedWeight"] = weight
            item["wasteReason"] = reason
            waste_items.append(item)
    selected_items = []
    total_weight = 0
    total_volume = 0
    for item in waste_items:
        weight = item.get("calculatedWeight", 0)
        if total_weight + weight <= max_weight:
            selected_items.append(item)
            total_weight += weight
            total_volume += item.get("calculatedVolume", 0)
    return_plan_steps = []
    retrieval_steps = []
    step_counter = 1
    for item in selected_items:
        return_plan_steps.append({
            "step": step_counter,
            "itemId": item.get("itemId", ""),
            "itemName": item.get("name", "Unknown"),
            "fromContainer": item.get("containerId", "Not Placed"),
            "toContainer": undocking_container_id
        })
        retrieval_steps.append({
            "step": step_counter,
            "action": "retrieve",
            "placeBack": False,
            "itemId": item.get("itemId", ""),
            "itemName": item.get("name", "Unknown")
        })
        step_counter += 1
    return_manifest = {
        "undockingContainerId": undocking_container_id,
        "undockingDate": undocking_date,
        "returnItems": [
            {
                "itemId": item.get("itemId", ""),
                "name": item.get("name", "Unknown"),
                "reason": item.get("wasteReason", "")
            }
            for item in selected_items
        ],
        "totalVolume": total_volume,
        "totalWeight": total_weight
    }
    response = {
        "success": True,
        "returnPlan": return_plan_steps,
        "retrievalSteps": retrieval_steps,
        "returnManifest": return_manifest
    }
    containers_data = load_json("containers.json", {"containers": []})
    if isinstance(containers_data, list):
        containers = containers_data
    else:
        containers = containers_data.get("containers", [])
    exists = any(c.get("containerId") == undocking_container_id for c in containers)
    if not exists:
        new_container = {
            "containerId": undocking_container_id,
            "zone": "Waste",
            "width": 0,
            "height": 0,
            "depth": 0
        }
        containers.append(new_container)
        if isinstance(containers_data, dict):
            containers_data["containers"] = containers
            save_json("containers.json", containers_data)
        else:
            save_json("containers.json", containers)
    placement_data = load_json("placement.json", {"placements": []})
    placements = placement_data.get("placements", [])
    updated_placements = []
    for p in placements:
        if any(item.get("itemId") == p.get("itemId") for item in selected_items):
            updated_placements.append({
                "itemId": p.get("itemId"),
                "containerId": undocking_container_id,
                "position": {
                    "startCoordinates": {"width": 0, "depth": 0, "height": 0},
                    "endCoordinates": {"width": 0, "depth": 0, "height": 0}
                }
            })
        else:
            updated_placements.append(p)
    placement_data["placements"] = updated_placements
    save_json("placement.json", placement_data)
    record_log("system", "WASTE_RETURN_PLAN", "", "Various", undocking_container_id, "Waste return plan generated")
    return jsonify(response)

# 9. Waste Management - Complete Undocking (/api/waste/complete-undocking)
@app.route("/api/waste/complete-undocking", methods=["POST"])
def waste_complete_undocking():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Invalid JSON"}), 400
    undocking_container_id = data.get("undockingContainerId")
    timestamp = data.get("timestamp")
    if not undocking_container_id or not timestamp:
        return jsonify({"success": False, "message": "Missing parameter(s)"}), 400
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except Exception:
        return jsonify({"success": False, "message": "Invalid timestamp"}), 400
    placement_data = load_json("placement.json", {"placements": []})
    placements = placement_data.get("placements", [])
    waste_item_ids = [p.get("itemId") for p in placements if p.get("containerId") == undocking_container_id]
    new_placements = [p for p in placements if p.get("containerId") != undocking_container_id]
    placement_data["placements"] = new_placements
    save_json("placement.json", placement_data)
    items_data = load_json("items.json", {"items": []})
    if isinstance(items_data, list):
        items = items_data
    else:
        items = items_data.get("items", [])
    original_count = len(items)
    new_items = [item for item in items if item.get("itemId") not in waste_item_ids]
    items_removed = original_count - len(new_items)
    if isinstance(items_data, list):
        save_json("items.json", new_items)
    else:
        items_data["items"] = new_items
        save_json("items.json", items_data)
    containers_data = load_json("containers.json", {"containers": []})
    if isinstance(containers_data, list):
        containers = containers_data
    else:
        containers = containers_data.get("containers", [])
    new_containers = [c for c in containers if c.get("containerId") != undocking_container_id]
    if isinstance(containers_data, list):
        save_json("containers.json", new_containers)
    else:
        containers_data["containers"] = new_containers
        save_json("containers.json", containers_data)
    record_log("system", "COMPLETE_UNDOCKING", "", undocking_container_id, "Removed", f"Undocking completed at {timestamp}")
    return jsonify({"success": True, "itemsRemoved": items_removed})

# 10. Export Arrangement (/api/export/arrangement)
@app.route("/api/export/arrangement", methods=["GET"])
def export_arrangement():
    arrangement_file = "placement.json"
    if not os.path.exists(arrangement_file):
        return Response("No arrangement file found.", status=404)
    try:
        with open(arrangement_file, "r") as f:
            data = json.load(f)
    except Exception as e:
        return Response(f"Error reading arrangement file: {str(e)}", status=500)
    placements = data.get("placements", [])
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Item ID", "Container ID", "Coordinates (W1,D1,H1)", "(W2,D2,H2)"])
    for placement in placements:
        item_id = placement.get("itemId", "")
        container_id = placement.get("containerId", "")
        position = placement.get("position", {})
        start_coords = position.get("startCoordinates", {})
        end_coords = position.get("endCoordinates", {})
        start_tuple = (start_coords.get("width", 0), start_coords.get("depth", 0), start_coords.get("height", 0))
        end_tuple = (end_coords.get("width", 0), end_coords.get("depth", 0), end_coords.get("height", 0))
        start_str = f"({start_tuple[0]},{start_tuple[1]},{start_tuple[2]})"
        end_str = f"({end_tuple[0]},{end_tuple[1]},{end_tuple[2]})"
        writer.writerow([item_id, container_id, start_str, end_str])
    csv_data = output.getvalue()
    output.close()
    response = Response(csv_data, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=arrangement.csv"
    return response

# 11. Time Simulation (/api/simulate/day)
@app.route("/api/simulate/day", methods=["POST"])
def simulate_day():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "Missing JSON data"}), 400
    num_of_days = data.get("numOfDays")
    to_timestamp = data.get("toTimestamp")
    if num_of_days is None and to_timestamp is None:
        return jsonify({"success": False, "message": "Either numOfDays or toTimestamp is required"}), 400
    items_to_be_used = data.get("itemsToBeUsedPerDay", [])
    simulation_file = "simulation.json"
    if os.path.exists(simulation_file):
        try:
            with open(simulation_file, "r") as f:
                sim_data = json.load(f)
            current_date_str = sim_data.get("currentDate")
            current_date = datetime.fromisoformat(current_date_str) if current_date_str else datetime.utcnow()
        except Exception:
            current_date = datetime.utcnow()
    else:
        current_date = datetime.utcnow()
    if to_timestamp:
        try:
            target_date = datetime.fromisoformat(to_timestamp)
        except Exception:
            return jsonify({"success": False, "message": "Invalid toTimestamp format"}), 400
        delta = target_date - current_date
        days_to_simulate = delta.days
        if days_to_simulate < 0:
            return jsonify({"success": False, "message": "toTimestamp is in the past"}), 400
    else:
        try:
            days_to_simulate = int(num_of_days)
        except Exception:
            return jsonify({"success": False, "message": "numOfDays must be a number"}), 400
        target_date = current_date + timedelta(days=days_to_simulate)
    items_used_total = {}
    items_depleted_today = {}
    items_expired = {}
    items_data = load_json("items.json", {"items": []})
    items_list = items_data.get("items", [])
    items_index = {item["itemId"]: item for item in items_list}
    for day in range(days_to_simulate):
        current_date += timedelta(days=1)
        for use_info in items_to_be_used:
            item_id = use_info.get("itemId")
            name = use_info.get("name")
            if item_id in items_index:
                item = items_index[item_id]
                expiry_date = None
                if item.get("expiryDate"):
                    try:
                        expiry_date = datetime.fromisoformat(item["expiryDate"])
                    except Exception:
                        pass
                if expiry_date and current_date >= expiry_date:
                    items_expired[item_id] = {"itemId": item_id, "name": name}
                else:
                    if item.get("usageLimit", 0) > 0:
                        item["usageLimit"] -= 1
                        items_used_total[item_id] = {"itemId": item_id, "name": name, "remainingUses": item["usageLimit"]}
                        if item["usageLimit"] == 0:
                            items_depleted_today[item_id] = {"itemId": item_id, "name": name}
    save_json("items.json", items_data)
    with open(simulation_file, "w") as f:
        json.dump({"currentDate": current_date.isoformat()}, f, indent=2)
    changes = {
        "itemsUsed": list(items_used_total.values()),
        "itemsExpired": list(items_expired.values()),
        "itemsDepletedToday": list(items_depleted_today.values())
    }
    response = {
        "success": True,
        "newDate": current_date.isoformat(),
        "changes": changes
    }
    return jsonify(response)

# 12. Logs (/api/logs) - GET with filtering
@app.route("/api/logs", methods=["GET"])
def get_logs():
    start_date_str = request.args.get("startDate")
    end_date_str = request.args.get("endDate")
    item_id_filter = request.args.get("itemId")
    user_id_filter = request.args.get("userId")
    action_type_filter = request.args.get("actionType")
    logs_data = load_json("logs.json", {"logs": []})
    filtered_logs = logs_data.get("logs", [])
    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str)
            filtered_logs = [log for log in filtered_logs if datetime.fromisoformat(log["timestamp"]) >= start_date]
        except Exception:
            pass
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str)
            filtered_logs = [log for log in filtered_logs if datetime.fromisoformat(log["timestamp"]) <= end_date]
        except Exception:
            pass
    if item_id_filter:
        filtered_logs = [log for log in filtered_logs if log.get("itemId") == item_id_filter]
    if user_id_filter:
        filtered_logs = [log for log in filtered_logs if log.get("userId") == user_id_filter]
    if action_type_filter:
        filtered_logs = [log for log in filtered_logs if log.get("actionType") == action_type_filter]
    return jsonify({"logs": filtered_logs})

# -----------------------
# Run the App
# -----------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
