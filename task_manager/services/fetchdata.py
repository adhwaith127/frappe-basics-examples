import frappe
from frappe.utils import format_datetime, time_diff_in_hours,today
from collections import defaultdict
from datetime import datetime

@frappe.whitelist(allow_guest=True)
def checkin_data(page,page_size,from_date=None,to_date=None,employee_name=None,department=None):
   
    page=int(page)  # =>  pagination code
    page_size=int(page_size)
    offset=(page-1)*page_size
    
    curr_date="2025-05-19"

    if not from_date and not to_date:
        emp_data=frappe.db.sql("""
        SELECT 
            ec.employee,
            ec.time,
            ec.log_type,
            em.department
        FROM
            `tabEmployee Checkin` ec
        LEFT JOIN 
            `tabEmployee` em ON ec.employee = em.name
        WHERE DATE(ec.time)=%s
        ORDER BY ec.employee asc
        LIMIT %s OFFSET %s
        """,(curr_date,page_size,offset),as_dict=True)

    grouped_data = defaultdict(lambda: defaultdict(list))
    for entry in emp_data:
        date_str = format_datetime(entry['time'], 'yyyy-MM-dd')
        grouped_data[entry['employee']][date_str].append(entry)
    fetched_data_length=len(grouped_data)

    
    display_data=[]
    for emp,days in grouped_data.items():
        for date,logs in days.items():
            logs=sorted(logs,key=lambda p:p['time'])
            total_hours=0.0
            shift_data={}
            i=0
            x=1
            while i<len(logs)-1:
                log1=logs[i]
                log2=logs[i+1]
                if log1['log_type']=="IN" and log2['log_type']=="OUT":
                    log1_time=log1['time']
                    log2_time=log2['time']
                    shift_time=time_diff_in_hours(log2_time,log1_time)
                    shift_time=round(shift_time,2)
                    total_hours+=shift_time
                    shift_data[f'Check_In_{x}']=log1_time.strftime("%I:%M")
                    shift_data[f'Check_Out_{x}']=log2_time.strftime("%I:%M")
                    i+=2
                    x+=1
                else:
                    i+=1

            entry_time=(logs[0]['time']).strftime("%I:%M")
            exit_time=(logs[-1]['time']).strftime("%I:%M")
            display_data.append({
                "employee":emp,
                "department":logs[0]['department'],
                "date":date,
                "Work Time":total_hours,
                "entry":entry_time,
                "exit":exit_time,
            })
    return {"display_data":display_data,"len":fetched_data_length}