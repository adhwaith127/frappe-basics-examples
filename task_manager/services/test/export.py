import io
import frappe
import calendar
import pandas as pd
from datetime import datetime, date, timedelta
from collections import OrderedDict, defaultdict
from typing import List, Dict, Any, Tuple, Optional, Set
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate


def _calculate_daily_work_hours(logs):
    # Handle absent case with complete structure
    try:
        if not logs:
            return {
                "employee": None, "department": None,
                "date": None, "work_time": 0.0, "entry_time": None,
                "exit_time": None, "status": "Absent", "checkin_pairs": []
            }

        total_hours = 0.0
        last_in_time = None
        first_in_time = None
        last_out_time = None
        checkin_pairs = []

        for log in logs:
            if log['log_type'] == "IN":
                if first_in_time is None:
                    first_in_time = log['time']
                if last_in_time is None:
                    last_in_time = log['time']

            elif log['log_type'] == "OUT":
                if last_in_time:
                    work_duration = time_diff_in_hours(log['time'], last_in_time)
                    total_hours += work_duration
                    checkin_pairs.append({
                        "in_time": last_in_time.strftime("%H:%M"),
                        "out_time": log['time'].strftime("%H:%M"),
                        "duration": round(work_duration, 2)
                    })
                    last_in_time = None
                
                last_out_time = log['time']
        
        # Handle unmatched IN entries
        if last_in_time:
            checkin_pairs.append({
                "in_time": last_in_time.strftime("%H:%M"),
                "out_time": None,
                "duration": None
            })

        # Absent only if no logs at all
        if not first_in_time and not last_out_time:
            status = "Absent"
        else:
            status = "Present"

        formatted_checkin_pairs = {}
        for pos,pair in enumerate(checkin_pairs,1):
            if pair['in_time']:
                formatted_checkin_pairs[f"In {pos}"]=pair['in_time']
            if pair['out_time']:
                formatted_checkin_pairs[f"Out {pos}"]=pair['out_time']

        result= {
            "Employee": logs[0]['employee'],
            "Department": logs[0]['department'],
            "Date": (logs[0]['time'].strftime("%d/%m/%Y")),
            "Daily Hours": round(total_hours, 2),
            "Entry": first_in_time.strftime("%H:%M") if first_in_time else None,
            "Exit": last_out_time.strftime("%H:%M") if last_out_time else None,
            "Status": status,
        }

        result.update(formatted_checkin_pairs)

        return result

    except Exception as e:
        frappe.log_error(f"Error in calculation: {str(e)}")
        return []


def _get_employee_data(start,end,emp=None,dept=None):
    try:
        conditions="DATE(ec.time) BETWEEN %(start)s AND %(end)s"
        params={"start":start,"end":end}

        if emp:
            conditions+="AND em.name= %(emp)s"
            params["emp"]=emp
        if dept:
            conditions+="AND em.department= %(dept)s"
            params["dept"]=dept

        query = f"""
            SELECT
                ec.employee, ec.time, ec.log_type,em.department
            FROM `tabEmployee Checkin` AS ec
            JOIN `tabEmployee` AS em ON ec.employee = em.name
            WHERE {conditions} AND em.status = 'Active'
            ORDER BY ec.employee, ec.time
        """

        raw_data = frappe.db.sql(query, params, as_dict=True)
        if not raw_data: return []

        grouped_data=defaultdict(lambda:defaultdict(list))
        for entry in raw_data:
            date_str=format_datetime(entry['time'],"yyyy-MM-DD")
            grouped_data[entry['employee']][date_str].append(entry)

        daily_summaries = []
        for employee, days in grouped_data.items():
            for date_key, logs in days.items():
                if logs:
                    daily_summary = _calculate_daily_work_hours(logs)
                    daily_summaries.append(daily_summary)
        
        return daily_summaries

    except Exception as e:
        frappe.log_error(f"Error fetching employee data: {str(e)}")
        return []

@frappe.whitelist()
def export_attendance(start,end,emp=None,dept=None):
    try:
        if not start or not end:
            raise ValueError("Both dates are needed !!")
        
        factored_data=_get_employee_data(start,end,emp,dept)

        if not factored_data:
            csv_content = "No attendance data found for the specified criteria.\n"
        else:
            df=pd.DataFrame(factored_data)  #this convert our row data to dataframe

            # Convert DataFrame to CSV string
            csv_content = df.to_csv(index=False, quoting=1)

            # Generate dynamic filename matching frontend expectation
            filename = f"attendance_report_{start}_to_{end}.csv"
            
            frappe.response['result'] = csv_content
            frappe.response['doctype']="Employee_Checkin"
            frappe.response['type'] = 'csv'

    except ValueError as ve:
        frappe.log_error(f"Missing dates: {str(ve)}")
        return {"error": "Missing required dates"}
    except Exception as e:
        frappe.log_error(f"Error in main export: {str(e)}")
        return {"error": "Export failed"}