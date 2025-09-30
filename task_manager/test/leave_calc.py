import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional


def _get_working_day_value(day_classification, leaves, employee_name, day_str):
    if day_classification == "full_day_leave":
        return 0.0
    elif day_classification == "half_day_leave":
        return 0.5
    elif day_classification in ["working_holiday", "regular_working"]:
        return 1.0
    else:  # holiday_priority, non_working_holiday, regular_non_working
        return 0.0


def _classify_day(has_checkin, has_leave, has_holiday, leaves, employee_name, day_str):
    # Priority 1: Leave + Holiday = Holiday
    if has_leave and has_holiday:
        return "holiday_priority"
    
    # Priority 2: Full day leave
    if has_leave:
        leave_info = leaves[employee_name][day_str]
        if not leave_info.get('half_day'):
            return "full_day_leave"
        else:
            return "half_day_leave"
    
    # Priority 3: Holiday with checkin = working day
    if has_holiday and has_checkin:
        return "working_holiday"
    
    # Priority 4: Holiday without checkin = non-working
    if has_holiday and not has_checkin:
        return "non_working_holiday"
    
    # Priority 5: Regular day with checkin
    if has_checkin:
        return "regular_working"
    
    # Priority 6: Regular day without checkin
    return "regular_non_working"


def _calculate_employee_working_days(employee_name, start_date, end_date, emp_data, leaves, holidays):
    total_working_days = 0.0
    daily_breakdown = {}
    
    current_date = start_date
    while current_date <= end_date:
        day_str = current_date.strftime("%Y-%m-%d")
        
        # Get data for this day
        has_checkin = day_str in emp_data and len(emp_data[day_str]) > 0
        has_leave = employee_name in leaves and day_str in leaves[employee_name]
        has_holiday = employee_name in holidays and current_date in holidays[employee_name]
        
        # Classify the day
        day_classification = _classify_day(has_checkin, has_leave, has_holiday, leaves, employee_name, day_str)
        
        # Get working day value
        working_day_value = _get_working_day_value(day_classification, leaves, employee_name, day_str)
        
        total_working_days += working_day_value
        daily_breakdown[day_str] = {
            'classification': day_classification,
            'working_day_value': working_day_value,
            'has_checkin': has_checkin,
            'has_leave': has_leave,
            'has_holiday': has_holiday
        }
        
        current_date += timedelta(days=1)
    
    return {
        'total_working_days': total_working_days,
        'daily_breakdown': daily_breakdown,
        'employee_info': emp_data.get('employee_info', {})
    }


def _get_employee_working_days(start_date,end_date,employee_data,employee_leaves,employee_holidays):
    # Convert dates
    if not isinstance(start_date, (datetime, date)):
        start_date = getdate(start_date)
    if not isinstance(end_date, (datetime, date)):
        end_date = getdate(end_date)
    
    working_days_result = {}
    
    # For each employee
    for employee_name, emp_data in employee_data.items():
        working_days_result[employee_name] = _calculate_employee_working_days(
            employee_name, start_date, end_date, emp_data, 
            employee_leaves, employee_holidays
        )
    
    return working_days_result
    

def _get_holiday(from_date,to_date):
    try:
        query="""
            SELECT em.name as employee, h.holiday_date
            FROM `tabEmployee` em 
            LEFT JOIN `tabHoliday` h ON em.holiday_list=h.parent
                AND h.holiday_date BETWEEN %(from_date)s AND %(to_date)s
            WHERE em.status='Active'
            ORDER BY em.name
        """
        emp_holidays=frappe.db.sql(query,values={"from_date":from_date,"to_date":to_date},as_dict=True)

        filtered_holidays=defaultdict(set)
        for holiday in emp_holidays:
            employee=holiday.pop('employee')
            holiday_date=holiday.pop('holiday_date')
            filtered_holidays[employee].add(holiday_date)
        
        return filtered_holidays
   
    except Exception as e:
        return ("Error",str(e))


def _get_leave(from_date,to_date):
    try:
        query="""
            SELECT em.name,em.employee_name,
            la.employee, la.from_date, la.to_date,la.leave_type,la.half_day,la.half_day_date
            FROM `tabEmployee` as em
            LEFT JOIN `tabLeave Application` as la on em.name=la.employee
            WHERE la.status = 'Approved' AND em.status='Active'
            AND la.docstatus = 1 AND (la.from_date BETWEEN %(from_date)s and %(to_date)s OR
            la.to_date BETWEEN %(from_date)s and %(to_date)s)
        """
        leaves=frappe.db.sql(query,values={"from_date":from_date,"to_date":to_date},as_dict=True)

        filtered_leaves=defaultdict(dict)
        for leave in leaves:
            employee = leave['name']
            leave_from = leave['from_date']
            leave_to = leave['to_date']

            current_date = leave_from
            while current_date <= leave_to:
                filtered_leaves[employee][str(current_date)] = {
                    "leave_type": leave['leave_type'],
                    "half_day": leave['half_day'],
                    "half_day_date": str(leave['half_day_date']) if leave['half_day_date'] else None
                }
                current_date += timedelta(days=1)
        
        return filtered_leaves
    
    except Exception as e:
        return ("Error",str(e))
    

def _get_employee_data(start_date, end_date):
    try:
        query = """
                SELECT
                    em.image,em.name,em.employee_name as emp_display_name,
                    em.department, em.reports_to, em.default_shift, em.holiday_list,
                    ec.time, ec.log_type
                FROM `tabEmployee` AS em
                LEFT JOIN `tabEmployee Checkin` AS ec ON ec.employee = em.name
                WHERE DATE(ec.time) BETWEEN %s and %s AND em.status = 'Active'
                ORDER BY ec.employee, ec.time
            """
        raw_checkin_data = frappe.db.sql(query, (start_date, end_date), as_dict=True)
        
        # these are fields we only want once
        employee_info_keys=[
            'image','name','department','reports_to',
            'holiday_list','emp_display_name','default_shift'
        ]
        
        sorted_data = defaultdict(lambda: defaultdict(list))
        processed_employees = set()  # Track which employees we've seen

        for record in raw_checkin_data:
            employee = record['name']
            
            # Handle employee info (only once per employee)
            if employee not in processed_employees:
                employee_info = {key: record[key] for key in employee_info_keys if key in record}
                sorted_data[employee]['employee_info'] = employee_info
                processed_employees.add(employee)

            # Handle checkin data
            checkin_data = {'time': record['time'], 'log_type': record['log_type']}
            checkin_date = getdate(record['time']).strftime("%Y-%m-%d")
            sorted_data[employee][checkin_date].append(checkin_data)
        
        return sorted_data

    except Exception as e:
        frappe.log_error("Error in employee data sql", str(e))
        return []


@frappe.whitelist()
def main_fn(select_date=None):
    try:
        if select_date is None:
            select_date=getdate(today())
        else:
            select_date=getdate(select_date)

        month_start=select_date.replace(day=1)
        _,num_days_in_month=calendar.monthrange(select_date.year,select_date.month)

        month_end=select_date.replace(day=num_days_in_month)

        # cap at selected date only if the date is current
        present_day = date.today()
        if select_date.year == present_day.year and select_date.month == present_day.month:
            month_end = min(present_day, month_end)
        
        num_days_in_period=0
        current_date=month_start
        while current_date<=month_end:
            num_days_in_period+=1
            current_date+=timedelta(days=1)

        # Get all data
        employee_data = _get_employee_data(month_start, month_end)
        employee_leaves = _get_leave(month_start, month_end)
        employee_holidays = _get_holiday(month_start, month_end)
        
        # Calculate working days
        employee_working_days = _get_employee_working_days(
            month_start, month_end, employee_data, 
            employee_leaves, employee_holidays
        )
        
        # Return the working days calculation
        return employee_working_days

    except Exception as e:
        return ("ERROR", str(e))