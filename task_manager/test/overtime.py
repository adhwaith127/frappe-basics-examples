import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional, Set


def _convert_timedelta(td):
    if td is None:
        return None
    
    if isinstance(td,timedelta):
        total_sec=td.total_seconds()
        total_hours=total_sec/3600

        return total_hours


def _processed_ot_data(ot_data,ot_date):
    overtime_data=defaultdict(lambda: {
        "employee_name": None,
        "reports_to": None,
        "department": None,
        "image": None,
        "first_in": None,
        "last_out": None,
        "total_overtime": None,
        "sessions": []
    })

    for row in ot_data:
        emp_id=row['employee']

        if overtime_data[emp_id]["employee_name"] is None:
            overtime_data[emp_id].update({
                "employee_name": row['employee_name'],
                "reports_to": row['reports_to'],
                "department": row['department'],
                "image": row['image'],
                "total_overtime": row['total_overtime']
                })
                
        if row['session_time'] is not None:
            session_time=row['session_time']
            session={
                "session_time":session_time,
                "log_type":row['log_type'],
                "session_order":row['session_order']
            }
            overtime_data[emp_id]['sessions'].append(session)

    result=[]
    for emp_id,emp_data in overtime_data.items():
        sessions=emp_data['sessions']

        if sessions:
            in_sessions=[]
            out_sessions=[]
            for s in sessions:
                if s.get('log_type')=="IN":
                    in_sessions.append(s)
                if s.get('log_type')=="OUT":
                    out_sessions.append(s)
            if in_sessions:
                first_in=min(in_sessions,key=lambda x:x['session_order'])
                emp_data['first_in']=first_in['session_time']
            
            if out_sessions:
                last_out=max(out_sessions,key=lambda x:x['session_order'])
                emp_data['last_out']=last_out['session_time']
        
        result.append(emp_data)
    
    return result


def _get_employee_overtime(ot_date):
    try:
        query = """
                    SELECT 
                    oc.date,
                    oc.employee,
                    oc.name as parent_id,
                    oc.total_overtime as total_overtime,
                    em.reports_to,
                    em.department,
                    em.employee_name,
                    em.image,
                    os.log_type,
                    os.session_duration,
                    os.idx as session_order,
                    os.time as session_time
                FROM `tabOvertime Checkin` AS oc
                JOIN `tabEmployee` AS em ON oc.employee = em.name
                LEFT JOIN `tabOvertime Sessions` AS os ON os.parent = oc.name
                WHERE DATE(oc.date) = %(ot_date)s
                AND em.status = 'Active'
                ORDER BY oc.employee, os.idx
            """
        params={"ot_date":ot_date}
        ot_data=frappe.db.sql(query,params,as_dict=True)

        if not ot_data: 
            return []        
        
        return _processed_ot_data(ot_data,ot_date)
        
    except Exception as e:
        frappe.log_error(f"Error in getting overtime data,{str(e)}")
        return []


@frappe.whitelist(allow_guest=True)
def fetch_overtime(ot_date):
    try:
        if not ot_date:
            raise ValueError("ot_date absent")
        ot_date=getdate(ot_date)
        
        overtime_data=_get_employee_overtime(ot_date)

        return overtime_data

    except ValueError as ve:
        frappe.throw(f"No overtime date provided,{str(ve)}")
        return []