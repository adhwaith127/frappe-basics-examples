import frappe
from frappe import _
from frappe.model.naming import make_autoname
from datetime import datetime
import pytz


@frappe.whitelist(allow_guest=True)
def add_checkin(punchingcode, employee_name, time, device_id):
    try:
        # Step 1: Get employee by biometric ID
        employee = frappe.db.get_value(
            "Employee",
            {"attendance_device_id": punchingcode},
            ["name", "employee_name"]
        )

        if not employee:
            frappe.throw(_("No Employee found for Biometric ID: {0}").format(punchingcode))

        employee_id, full_name = employee
        checkin_time = datetime.strptime(time, "%d-%m-%Y %H:%M:%S")
        checkin_date = checkin_time.date() 

        if (employee_id and full_name and checkin_time and checkin_date and device_id):
            return handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id)
        else:
            frappe.throw("Data missing")

    except Exception as e:
        frappe.log_error(f"Error in add_checkin: {str(e)}", "Biometric API Error")
        return {"error":str(e)}



def handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location=None):
    """
    Handle Employee Checkin - creates new row for each punch
    """
    # Determine log_type by checking last entry for the day
    log_type = "IN"
    last_log_type = frappe.db.sql(
        """
        SELECT log_type FROM `tabEmployee Checkin`
        WHERE employee = %s AND DATE(time) = %s
        ORDER BY time DESC LIMIT 1
        """,
        (employee_id, checkin_date),
        as_dict=False
    )

    if last_log_type:
        log_type = "OUT" if last_log_type[0][0] == "IN" else "IN"

    # Generate name using naming series
    name = make_autoname('CHKIN-.#####')

    # Insert new checkin record
    ist = pytz.timezone('Asia/Kolkata')
    current_ist = datetime.now(ist).replace(microsecond=0)

    # Creates proper Frappe document
    employee_checkin = {
        "doctype": "Employee Checkin",
        "employee": employee_id,
        "employee_name":full_name,
        "creation":current_ist,
        "modified":current_ist,
        "modified_by":frappe.session.user,
        "owner":frappe.session.user,
        "log_type": log_type,
        "time": checkin_time,
        "device_id":device_id
    }
    try:
        frappe.get_doc(employee_checkin).insert(ignore_permissions=True)
        frappe.db.commit()

        return {
            "status":"success",
            "employee":employee_id,
            "employee_name":full_name,
            "timestamp":checkin_time
        }

    except Exception as e:
        frappe.log_error(frappe.get_traceback(),"Error in adding checkin")
        return {"error":str(e)}