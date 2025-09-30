import frappe
from frappe.utils import get_datetime, getdate, time_diff_in_seconds
from frappe import _
from frappe.model.naming import make_autoname
from datetime import datetime, timedelta, time
import pytz
import pymysql

def handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id):
    """
    Create Employee Checkin Log 
    """
    try:    
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
        
        frappe.db.sql("""
            INSERT INTO `tabEmployee Checkin`
            (name, creation, modified, modified_by, owner, docstatus, idx,
            employee, employee_name, time, device_id, log_type)
            VALUES (%s,%s, %s, %s, %s, 0, 0,
            %s, %s, %s, %s, %s)
        """, (
            name, current_ist, current_ist,frappe.session.user, frappe.session.user,
            employee_id, full_name, checkin_time, device_id, log_type
        ))

        return {
            "status": "success",
            "name": name,
            "log_type": log_type,
            "checkin_time": checkin_time,
            "table": "Employee Checkin"
        }
    
    except pymysql.err.ProgrammingError as e:
        frappe.log_error(f"SQL syntax or column error: {str(e)}", "Sql Entry error")
        frappe.throw(_("Database query error."))
    except Exception as e:
        frappe.throw(_("Error in adding employee checkin: {0}").format(str(e)))


@frappe.whitelist(allow_guest=True)
def add_checkin(punchingcode, employee_name, time, device_id):
    """
    Main API
    """
    try:
        # Get employee by biometric ID
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

        if not checkin_date or not checkin_time:
            frappe.throw(_("Missing checkin time"))

        if None not in(employee_id, full_name, checkin_time, checkin_date, device_id):
            return handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id)

    except frappe.DoesNotExistError as e:
        frappe.log_error(f"Employee not found: {str(e)}", "Biometric API Error")
        frappe.throw(_("Employee not found."))
    except Exception as e:
        frappe.log_error(f"Error in add_checkin: {str(e)}", "Biometric API Error")
        frappe.throw(_("Error processing checkin: {0}").format(str(e)))
