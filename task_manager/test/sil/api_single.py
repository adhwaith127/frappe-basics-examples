import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional, Set

def _get_employee_data(select_date):
    try:
        query = """
                SELECT
                    ec.employee, ec.time, ec.log_type,
                    em.name, em.department, em.reports_to, em.default_shift, em.holiday_list, em.image,
                    st.end_time
                FROM `tabEmployee Checkin` AS ec
                JOIN `tabEmployee` AS em ON ec.employee = em.name
                LEFT JOIN `tabShift Type` AS st on em.default_shift=st.name
                WHERE DATE(ec.time) = %s AND em.status = 'Active' AND em.name=%s
                ORDER BY ec.employee, ec.time
            """
        raw_checkin_data = frappe.db.sql(query, select_date, as_dict=True)
        
        return raw_checkin_data
    
    except Exception as e:
        frappe.log_error("Error in employee data sql", str(e))
        return []


@frappe.whitelist(allow_guest=True)
def get_employee_details(user_id):
    """
    Fetch employee details by linked user_id.
    Returns name, department, designation, team etc.
    """

    # Check if user exists
    if not frappe.db.exists("User", user_id):
        return {"success": False, "message": (f"User {user_id} not found")}

    # Find employee linked to this user
    employee = frappe.db.get_value(
        "Employee",
        {"user_id": user_id},
        ["name", "employee_name", "department", "designation"],
        as_dict=True
    )

    if not employee:
        return {"success": False, "message":(f"No Employee linked with user {user_id}")}
    
    select_date=today()
    emp_name=employee['employee_name']
    employee_data=_get_employee_data(select_date,emp_name)
    
    return employee_data

    # return {
    #     "success": True,
    #     "data": employee
    # }
