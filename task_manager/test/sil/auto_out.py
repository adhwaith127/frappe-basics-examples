import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta,time

# !!!!! softland
def _close_log_sl(unclosed_logs,checkin_date):
    try:
        for log in unclosed_logs:
            employee=log['name']
            employee_name=log['employee_name']
            department=log['department']
            last_in=log['time']
            last_in_device=log['device_id']
            if isinstance(last_in,str):
                last_in_datetime = datetime.strptime(last_in, "%Y-%m-%d %H:%M:%S")        
                last_in_date = last_in_datetime.date()
                last_in_time = last_in_datetime.time()
            elif isinstance(last_in,datetime):
                last_in_date=last_in.date()
                last_in_time=last_in.time()
            else:
                continue

            auto_out_time=last_in_time
            auto_out=datetime.combine(last_in_date,auto_out_time)

            doc=frappe.new_doc("Employee Checkin")
            doc.employee=employee
            doc.employee_name=employee_name
            doc.log_type="OUT"
            doc.time=auto_out
            doc.device_id=last_in_device
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
        
        return {"status": "success"}
    
    except Exception as e:
        frappe.log_error("Error in adding auto checkout",str(e))
        return  {"error":"Unsuccessful auto logging"}


# !!!!!!!!!! cleanplus
def _close_log_cp(unclosed_logs,checkin_date):
    try:
        for log in unclosed_logs:
            employee=log['name']
            employee_name=log['employee_name']
            department=log['department']
            last_in=log['time']
            last_in_device=log['device_id']
            shift_end=log['shift_end']
            if isinstance(last_in,str):
                last_in_datetime = datetime.strptime(last_in, "%Y-%m-%d %H:%M:%S")        
                last_in_date = last_in_datetime.date()
                last_in_time = last_in_datetime.time()
            elif isinstance(last_in,datetime):
                last_in_date=last_in.date()
                last_in_time=last_in.time()
            else:
                continue

            if isinstance(shift_end,timedelta):
                shift_end_str=str(shift_end)
                shift_end=datetime.strptime(shift_end_str,"%H:%M:%S").time()
        
            # we can use .get_doc() or .new_doc() . if we use .get we can pass dict for creating new entry
            # if .new_doc is used we set all individual fields on our own
            if last_in_time>=shift_end:
                auto_out_time=last_in_time
            else:
                auto_out_time=shift_end

            auto_out=datetime.combine(last_in_date,auto_out_time)

            doc=frappe.new_doc("Employee Checkin")
            doc.employee=employee
            doc.employee_name=employee_name
            doc.log_type="OUT"
            doc.time=auto_out
            doc.device_id=last_in_device
            doc.insert(ignore_permissions=True)
            frappe.db.commit()
        
        return {"status": "success"}
    
    except Exception as e:
        frappe.log_error("Error in adding auto checkout",str(e))
        return  {"error":"Unsuccessful auto logging"}
    
# here we get all unclosed logs(logs with last log as in)
def _get_unclosed_logs(logs):
    try:
        no_checkin=[]
        closed=[]
        unclosed=[]
        for log in logs:
            if not log['employee'] or not log['employee'] or not log['employee']:
                no_checkin.append(log)
            if log['log_type']=="OUT":
                closed.append(log)
            if log['log_type']=="IN":
                unclosed.append(log)
        
        return unclosed
    
    except Exception as e:
        frappe.log_error("Error in classifying logs",str(e))
        return []

# get all employees' last log only with the help of subquery
def _get_raw_checkin_data(checkin_date):
    try:
        query = """
                SELECT
                    em.name,
                    em.employee_name,
                    em.department, 
                    ec.employee, 
                    ec.time, 
                    ec.log_type,
                    ec.device_id,
                    st.end_time AS shift_end
                FROM `tabEmployee` AS em
                LEFT JOIN (
                    SELECT
                        employee,
                        MAX(time) AS time,
                        SUBSTRING_INDEX(GROUP_CONCAT(log_type ORDER BY time DESC), ',', 1) AS log_type,
                        SUBSTRING_INDEX(GROUP_CONCAT(device_id ORDER BY time DESC), ',', 1) AS device_id
                    FROM `tabEmployee Checkin`
                    WHERE DATE(time) = %s
                    GROUP BY employee
                ) AS ec ON ec.employee = em.name
                LEFT JOIN `tabShift Type` AS st ON em.default_shift = st.name
                WHERE em.status = 'Active'
                ORDER BY em.name;
            """
        raw_checkin_data = frappe.db.sql(query, (checkin_date,), as_dict=True)
        
        return raw_checkin_data
    
    except Exception as e:
        frappe.log_error("Error in getting checkin data query", str(e))
        return []
    
# !!!!!!!!!! cleanplus
@frappe.whitelist(allow_guest=True)
def closeAttendanceCP(checkin_date=None):
    try:
        if checkin_date is None or not checkin_date:
            checkin_date=getdate(today())

        raw_checkin_data=_get_raw_checkin_data(checkin_date)

        pending_out_logs=_get_unclosed_logs(raw_checkin_data)
        
        result=_close_log_cp(pending_out_logs,checkin_date)
        
        return result
    
    except Exception as e:
        frappe.log_error("Error in closing attendance",str(e))
        return {"error":"Error in closing attendance"}
    

# !!!!! softland
@frappe.whitelist(allow_guest=True)
def closeAttendanceSL(checkin_date=None):
    try:
        if checkin_date is None or not checkin_date:
            checkin_date=getdate(today())

        raw_checkin_data=_get_raw_checkin_data(checkin_date)

        pending_out_logs=_get_unclosed_logs(raw_checkin_data)
        
        result=_close_log_sl(pending_out_logs,checkin_date)
        
        return result
    
    except Exception as e:
        frappe.log_error("Error in closing attendance",str(e))
        return {"error":"Error in closing attendance"}