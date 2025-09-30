import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional, Set


def _calculate_employee_work_hours(logs,shift_end=None):
    try:
        if not logs:
            return {
                "employee":None,"department":None,"reports_to":None,
                "date":None,"daily_working_hours":0.0,"entry":None,"exit":None,"checkin_pairs": []
            }

        total_working_hours = 0.0
        last_in_time = None
        first_in_time = None
        last_out_time = None
        checkin_pairs = []

        for log in logs:
            if log['log_type']=="IN":
                if first_in_time is None:
                    first_in_time=log['time']
                if last_in_time is None:
                    last_in_time=log['time']
            elif log['log_type']=="OUT":
                if last_in_time:
                    session_duration=time_diff_in_hours(log['time'], last_in_time)
                    total_working_hours+=session_duration
                    checkin_pairs.append({
                        "In":last_in_time.strftime("%H:%M"),
                        "Out":log['time'].strftime("%H:%M"),
                        "Session":round(session_duration,2)
                    })
                    last_in_time=None

                last_out_time=log['time']
        
        if last_in_time and shift_end:
            session_duration=time_diff_in_hours(shift_end,last_in_time)
            total_working_hours += session_duration
            checkin_pairs.append({
                "In":last_in_time.strftime("%H:%M"),
                "Out":shift_end.strftime("%H:%M"),
                "Session":round(session_duration,2)
            })

        if last_in_time and shift_end is None:
            checkin_pairs.append({
                "In":last_in_time.strftime("%H:%M"),
                "Out":last_in_time.strftime("%H:%M"),
                "Session":0.0
            })

        return{
            "employee":logs[0]['employee'],
            "department":logs[0]['department'],
            "reports_to":logs[0]['reports_to'],
            "date": format_datetime(logs[0]['time'], 'yyyy-MM-dd'),
            "daily_working_hours":round(total_working_hours,2),
            "entry": first_in_time.strftime("%H:%M") if first_in_time else None,
            "exit": last_out_time.strftime("%H:%M") if last_out_time else None,
            "checkin_pairs":checkin_pairs
        }

    except Exception as e:
        frappe.log_error("Error in calculating employee work hours",str(e)) 
        return {"error":"Error in calculating work hours"}


def _process_employee_data(raw_checkin_data):
    try:
        grouped_emp_data=defaultdict(lambda:defaultdict(list))
        for entry in raw_checkin_data:
            date_str=format_datetime(entry['time'],'yyyy-MM-dd')
            grouped_emp_data[entry['employee']][date_str].append(entry)

        daily_summaries=[]
        for employee,day in grouped_emp_data.items():
            for date,logs in day.items():
                if logs:
                    shift_end = logs[0]['end_time']
                    if isinstance(shift_end, timedelta):
                    # Convert timedelta to a time object <-- this fixed this sections' error !!!
                        shift_end = (datetime.min + shift_end).time()
                    elif isinstance(shift_end,str):   #check whether shift end is string or datetime then convert
                        shift_end = datetime.strptime(shift_end, "%H:%M").time()
                    elif isinstance(shift_end, datetime):  
                        shift_end = shift_end.time() 
                    
                #getdate gets the date part from datetime and combine with end time(str/time obj) for datetime
                    shift_end = datetime.combine(getdate(logs[0]['time']), shift_end)
                    daily_summary = _calculate_employee_work_hours(logs, shift_end)
                    daily_summaries.append(daily_summary)

        return daily_summaries

    except Exception as e:
        frappe.log_error("Error in processing checkin data",str(e))
        return {"error":"Error in processing employee data"}

def _get_employee_data(selected_date):
    try:
        query="""
            SELECT
                    ec.employee, ec.time, ec.log_type,
                    em.department, em.reports_to,em.default_shift,
                    st.end_time
                FROM `tabEmployee Checkin` AS ec
                JOIN `tabEmployee` AS em ON ec.employee = em.name
                JOIN `tabShift Type` AS st on em.default_shift=st.name
                WHERE DATE(ec.time) = %s AND em.status = 'Active'
                ORDER BY ec.employee, ec.time    
        """                         #frappe.db.sql expects parameters as a list/tuple. so pass accordingly   
        raw_checkin_data=frappe.db.sql(query,(selected_date,),as_dict=True)

        return _process_employee_data(raw_checkin_data)
    
    except TypeError as te:
        frappe.log_error("Error in db fetch",str(te))
        return {"error":"Error in getting employee checkin data"}
    except Exception as e:
        return {"error":"Error in getting employee checkin data"}

@frappe.whitelist(allow_guest=True)
def getDaily(selected_date=None):
    try:
        if selected_date is None:
            selected_date=today()

        selected_date=getdate(selected_date)

        calculated_employee_data=_get_employee_data(selected_date)

        if isinstance(calculated_employee_data, dict) and "error" in calculated_employee_data:
            return calculated_employee_data # Pass the error message back to the client

        # !!!!!!!!!!!!!!!!!! fetch threshold
        daily_threshold=8.0
        employee_below_average={}
        for emp in calculated_employee_data:
            #currently hardcoded threshold hours later fetch from datatype
            if emp['daily_working_hours']<daily_threshold:
                if emp['employee'] not in employee_below_average:
                    employee_below_average[emp['employee']]=[]
                employee_below_average[emp['employee']].append(emp)

        return employee_below_average

    except Exception as e:
        frappe.log_error("Error in main function",str(e))
        return {"error":"Error in main function"}