import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional,Set

def _get_leaves_for_period(start_date, end_date):
    try:
        leaves_query = """
            SELECT employee, from_date, to_date
            FROM `tabLeave Application`
            WHERE status = 'Approved'
            AND docstatus = 1
            AND (
                (from_date BETWEEN %(start_date)s AND %(end_date)s) OR
                (to_date BETWEEN %(start_date)s AND %(end_date)s) OR  
                (from_date <= %(start_date)s AND to_date >= %(end_date)s)
            )
        """
            
        leaves_data = frappe.db.sql(leaves_query, {
            "start_date": start_date,
            "end_date": end_date
        }, as_dict=True)

        employee_leaves = defaultdict(set)

        for leave in leaves_data:
            leave_start = max(leave.from_date, start_date)
            leave_end = min(leave.to_date, end_date)

            current_date = leave_start
            while current_date <= leave_end:
                employee_leaves[leave.employee].add(str(current_date))
                current_date += timedelta(days=1)
        
        return dict(employee_leaves)

    except Exception as e:
        frappe.log_error("Error in getting employee leaves", str(e))
        return {}

def _calculate_monthly_averages(daily_summaries,employee_holidays,num_days_in_month,employee_leaves):
    try:
        employee_monthly=defaultdict(list)
        for daily in daily_summaries:
            employee_monthly[daily['employee']].append(daily)

        monthly_results=[]
        for employee,days in employee_monthly.items():
            total_hours=0.0
            for day in days:
                total_hours+=day['daily_working_hours']

            holiday_dates=set()
            for d in employee_holidays.get(employee,[]):
                holiday_dates.add(str(d))
            leave_dates = employee_leaves.get(employee, set())

            non_working_days=len(holiday_dates.union(leave_dates))

            # Number of days in month monthdays minus holidays and leaves
            working_days=num_days_in_month-non_working_days

            # handle a case where working days might become negative
            working_days=max(working_days,0)

            if working_days>0:
                monthly_average=total_hours/working_days
            else:
                monthly_average=0.

            monthly_results.append({
                "employee":employee,
                'total_hours': total_hours,
                'working_days': working_days,
                'holidays_in_month': len(holiday_dates),
                'leaves_taken':len(leave_dates),
                'monthly_average': round(monthly_average, 2)
            })

        return monthly_results

    except Exception as e:
        frappe.log_error("Error in calculating monthly averages",str(e))
        return []


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
    

def _sort_checkin_data(filtered_checkin_data):
    try:
        grouped_emp_data=defaultdict(lambda:defaultdict(list))
        for entry in filtered_checkin_data:
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


def _filter_checkins(raw_checkin_data,employee_holidays,employee_leaves):
    try:
        filtered_data=[]
        for record in raw_checkin_data:
            employee=record['employee']
            checkin_date=getdate(record['time'])
            if (checkin_date not in employee_holidays.get(employee,[])) and (str(checkin_date) not in employee_leaves.get(employee,[])):
                filtered_data.append(record) 

        return filtered_data
    
    except Exception as e:
        frappe.log_error("Error in filtering checkins",str(e))
        return []

def _get_employee_holidays(month_start,month_end):
    try:
        holiday_query = """
            SELECT em.name as employee, em.holiday_list, h.holiday_date
            FROM `tabEmployee` em 
            LEFT JOIN `tabHoliday` h ON em.holiday_list=h.parent
                AND h.holiday_date BETWEEN %s AND %s
            WHERE em.status='Active'
                -- AND em.holiday_list is NOT NULL
            ORDER BY em.name
        """
        employee_holiday_data=frappe.db.sql(holiday_query,(month_start,month_end), as_dict=True)

        employee_holidays=defaultdict(list)

        for holiday in employee_holiday_data:
            emp=holiday['employee']
            if holiday['holiday_date']:
                employee_holidays[emp].append(getdate(holiday['holiday_date']))
            else:
                # ensures employees with no holiday list or no holidays in range exist in dict
                employee_holidays.setdefault(emp, [])

        employee_holidays=dict(employee_holidays)
        
        return employee_holidays

    except Exception as e:
        frappe.log_error("Error in getting employee holidays",str(e))
        return {}

def _get_employee_data(month_start,month_end):
    try:
        query ="""
                SELECT
                    ec.employee, ec.time, ec.log_type,
                    em.name,em.department, em.reports_to,em.default_shift,em.holiday_list,
                    st.end_time
                FROM `tabEmployee Checkin` AS ec
                JOIN `tabEmployee` AS em ON ec.employee = em.name
                LEFT JOIN `tabShift Type` AS st on em.default_shift=st.name
                WHERE DATE(ec.time) BETWEEN %s and %s AND em.status = 'Active'
                ORDER BY ec.employee, ec.time
            """
        raw_checkin_data=frappe.db.sql(query,(month_start,month_end),as_dict=True)
        
        return raw_checkin_data
    
    except Exception as e:
        frappe.log_error("Error in employee data sql",str(e))
        return []


@frappe.whitelist(allow_guest=True)
def fetchmonthly(selected_date=None):
    try:
        if selected_date is None:
            selected_date=today()

        selected_date=getdate(selected_date)

        month_start=selected_date.replace(day=1)
        _,num_days_in_month=calendar.monthrange(selected_date.year,selected_date.month)
        month_end=selected_date.replace(day=num_days_in_month)

        # here we get employees checkin data within the month boundaries
        raw_checkin_data=_get_employee_data(month_start,month_end)
        if not raw_checkin_data:
            return {"message": "No attendance data found for the selected month"}
        
        # here we find each employee's holidays with their name/id
        employee_holidays=_get_employee_holidays(month_start,month_end)
        if not employee_holidays:
            frappe.log_error("No holiday data found", "Holiday mapping is empty")

        # get employees approved leaves 
        employee_leaves = _get_leaves_for_period(start_date=month_start, end_date=month_end)

        # filter the checkin data to exclude holidays and that day's working data 
        filtered_checkin_data=_filter_checkins(raw_checkin_data,employee_holidays,employee_leaves)

        # finding each day's processed data with daily worktime and checkin pairs
        daily_summaries=_sort_checkin_data(filtered_checkin_data)
        if not daily_summaries:
            return {"message": "No valid working days found after filtering holidays"}

        # now we find monthly summaries
        monthly_averages=_calculate_monthly_averages(daily_summaries,employee_holidays,num_days_in_month,employee_leaves)
        if not monthly_averages:
            return {"message": "Could not calculate monthly averages"}

        # !!!!!!!!!!!!!!!!!! fetch threshold
        monthly_threshold=8.0
        employee_below_average={}
        for emp in monthly_averages:
            #currently hardcoded threshold hours later fetch from datatype
            if emp.get("monthly_average",0)<monthly_threshold:
                if emp['employee'] not in employee_below_average:
                    employee_below_average[emp['employee']]=[]
                employee_below_average[emp['employee']]={
                    'month_start':month_start.strftime('%Y-%m-%d'),
                    'month_end':month_end.strftime('%Y-%m-%d'),
                    'monthly_average':emp['monthly_average'],
                    'total_hours': emp['total_hours'],
                    'holidays_in_month':emp['holidays_in_month'],
                    'leaves_taken':emp['leaves_taken'],
                    'working_days': emp['working_days']
                }

        return employee_below_average
    
    except Exception as e:
        frappe.log_error("Error in main function",str(e))
        return {"error":"Error in main function"}