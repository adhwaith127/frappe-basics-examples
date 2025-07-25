import frappe
from frappe.utils import format_datetime, time_diff_in_hours,today
from frappe.utils.data import getdate
from collections import defaultdict
from datetime import datetime
from datetime import date

def get_checkin_data(from_date=None,to_date=None,specific_date=None):

    if from_date and to_date:
        date_condition="DATE(ec.time) BETWEEN %s AND %s"
        params=[from_date,to_date]
    elif specific_date:
        date_condition="DATE(ec.time) = %s"
        params=[specific_date]
    elif not from_date and not to_date and not specific_date:
        date_condition="DATE(ec.time) = %s"
        params=[today()]
    else:
        frappe.throw("Invalid date parameters. Provide either both from_date and to_date, or specific_date, or leave all empty for today's data.")

    base_query="""SELECT 
                ec.employee,
                ec.time,
                ec.log_type,
                em.department
            FROM
            `tabEmployee Checkin` ec
            LEFT JOIN 
            `tabEmployee` em ON ec.employee = em.name
            WHERE {date_condition}
            ORDER BY ec.employee ASC, ec.time ASC""".format(date_condition=date_condition)
    try:
        emp_data=frappe.db.sql(base_query,params,as_dict=True)
        if not emp_data:
            return {"message": "No checkin data found for the specified date range"}
        
        return emp_data
    except Exception as e:
        return {"error":f"DB fetch error {str(e)}"}


def sort_checkin_data(emp_data):
    grouped_data = defaultdict(lambda: defaultdict(list))
    for entry in emp_data:
        date_str = format_datetime(entry['time'], 'yyyy-MM-dd')
        grouped_data[entry['employee']][date_str].append(entry)
    return grouped_data

def calculate_work_hours(grouped_data):
    result=[]
    for emp,days in grouped_data.items():
        for date,logs in days.items():
            logs=sorted(logs,key=lambda p:p['time'])
            total_hours=0.0
            i=0
            while i<len(logs)-1:
                log1=logs[i]
                log2=logs[i+1]

                in_condition = log1['log_type'] == "IN"
                out_condition = log2['log_type'] == "OUT"
                
                if in_condition and out_condition:
                    log1_time=log1['time']
                    log2_time=log2['time']
                    shift_time=time_diff_in_hours(log2_time,log1_time)
                    shift_time=round(shift_time,2)
                    total_hours+=shift_time
                    i+=2
                else:
                    shift_time=0.0
                    total_hours+=shift_time
                    i+=1

            entry_time=(logs[0]['time']).strftime("%H:%M")
            exit_time=(logs[-1]['time']).strftime("%H:%M")
            result.append({
                "employee":emp,
                "department":logs[0]['department'],
                "date":date,
                "Work Time":total_hours,
                "entry":entry_time,
                "exit":exit_time,
            })

    return {"result":result,"total_count":len(result)}


def checkin_for_date_range(data):
    summary_data=defaultdict(lambda:{
        'total_work_hours':0.0,
        'days':0,
        'latest_entry':None,
        'earliest_exit':None,
        'department': None
    })

    for record in data['result']:
        name=record['employee']
        dept=record['department']

        emp=summary_data[name]  # since this (summary_data) is a default dict 
        emp['total_work_hours']+=record['Work Time'] # it creates an empty dict with fields declared above 
        emp['days']+=1          # siince no such key exist in db currently
        if emp['department']==dept:
            pass
        else:
            emp['department'] = dept
        
    result={}
    for name,stats in summary_data.items():
        result[name]={
            "average_work_hours":round(stats['total_work_hours']/stats['days'],2),
            "department":stats['department']
        }
    return {"result":result,"total_count":len(result)}

@frappe.whitelist(allow_guest=True)
def fetch_checkins(from_date=None, to_date=None, specific_date=None):
    try:
        if from_date and not to_date:
            frappe.throw("Provide To date")
        if to_date and not from_date:
            frappe.throw("Provide From date")

        emp_data = get_checkin_data(from_date, to_date, specific_date)
        
        if isinstance(emp_data, dict) and ('error' in emp_data or 'message' in emp_data):
            return emp_data
        
        grouped_data = sort_checkin_data(emp_data)
        result = calculate_work_hours(grouped_data)

        if from_date and to_date:
            result = checkin_for_date_range(result)

        return result
    
    except Exception as e:
        frappe.log_error(f"Error in fetching data from database: {str(e)}")
        return {"error": f"Processing error: {str(e)}"}