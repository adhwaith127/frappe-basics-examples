import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today
from frappe.utils.data import getdate
from collections import defaultdict
from datetime import datetime, date, timedelta
import calendar


#find employee data with date conditions
def get_checkin_data(from_date=None, to_date=None, specific_date=None): 
           
    # set date conditions

    if from_date and to_date:
        date_condition = "DATE(ec.time) BETWEEN %s AND %s"
        params = [from_date, to_date]
    elif specific_date:
        date_condition = "DATE(ec.time) = %s"
        params = [specific_date]
    else:
        frappe.throw("Invalid date parameters. Provide either both from_date and to_date, or specific_date, or leave all empty for today's data.")

    # Base query for db fetch
    base_query = """
        SELECT 
            ec.employee,
            ec.time,
            ec.log_type,
            em.department
        FROM
            `tabEmployee Checkin` ec
        LEFT JOIN 
            `tabEmployee` em ON ec.employee = em.name
        WHERE {date_condition}
        ORDER BY ec.employee ASC, ec.time ASC
    """.format(date_condition=date_condition)
    
    try:
        emp_data = frappe.db.sql(base_query, params, as_dict=True)
        
        if not emp_data:
            if specific_date and not from_date and not to_date:
                return {"message": f"No checkin data found for {specific_date}"}
            elif not specific_date and from_date and to_date:
                return {"message": f"No checkin data found between {from_date} and {to_date}"}
            return {"message": "No checkin data found for the specified date"}

        return emp_data
        
    except Exception as e:
        frappe.log_error(f"DB error: {str(e)}", "Check-in Fetch Failure")
        return {"error": "Failed to fetch check-in data."}


def sort_checkin_data(emp_data):

    #group checkin data with employee name and date

    grouped_data = defaultdict(lambda: defaultdict(list))
    
    for entry in emp_data:
        date_str = format_datetime(entry['time'], 'yyyy-MM-dd')
        grouped_data[entry['employee']][date_str].append(entry)
        
    return grouped_data

#find work hour by pairing entry log
def calculate_work_hours(grouped_data):        
    result = []
    
    for emp, days in grouped_data.items():
        for date, logs in days.items():
            # Sort logs by time
            logs = sorted(logs, key=lambda p: p['time'])
            total_hours = 0.0
            i = 0
            
            # find in/out pairs
            while i < len(logs) - 1:
                log1 = logs[i]
                log2 = logs[i + 1]

                in_condition = log1['log_type'] == "IN"
                out_condition = log2['log_type'] == "OUT"

                if in_condition and out_condition:
                    log1_time = log1['time']
                    log2_time = log2['time']
                    shift_time = time_diff_in_hours(log2_time, log1_time)
                    shift_time = round(shift_time, 2)
                    total_hours += shift_time
                    i += 2
                else:
                    shift_time = 0.0
                    total_hours += shift_time
                    i += 1

            # Format entry and exit times
            entry_time = (logs[0]['time']).strftime("%H:%M")
            exit_time = (logs[-1]['time']).strftime("%H:%M")
            
            result.append({
                "employee": emp,
                "department": logs[0]['department'],
                "date": date,
                "work_time": total_hours,
                "entry": entry_time,
                "exit": exit_time,
                "result_type": "daily_summary"
            })

    return {"result": result, "total_count": len(result)}


#find checkin data summary if provided with date range
def checkin_data_for_date_range(data):
    
    summary_data = defaultdict(lambda: {
        'total_work_hours': 0.0,
        'days': 0,
        'latest_entry': None,
        'earliest_exit': None,
        'department': None
    })

    # Aggregate data by employee
    for record in data['result']:
        name = record['employee']
        dept = record['department']

        emp = summary_data[name]
        emp['total_work_hours'] += record['work_time']
        emp['days'] += 1
        
        if emp['department'] != dept:
            emp['department'] = dept

    # building final output
    result = []
    for name, stats in summary_data.items():
        department = stats['department'] if stats['department'] is not None else "No department data available"
        
        employee_summary = {
            "employee": name,
            "department": department,
            "average_work_hours": round(stats['total_work_hours'] / stats['days'], 2),
            "total_working_days": stats['days'],
            "result_type": "daterange_summary"
        }
        result.append(employee_summary)
        
    return {"result": result, "total_count": len(result)}


#find working days between 2 dates(month start and end)
def working_days_count(first_day, last_day):
    
    total_working_days = 0
    current_date = first_day
    
    while current_date <= last_day:
        if current_date.weekday() < 5: #--> monday is 0 and friday is 4
            total_working_days += 1
        current_date += timedelta(days=1)
        
    return total_working_days


#create a complete list of all employees available from daily and monthly data inputs
def create_employee_registry(daily_data, monthly_data):
    
    employee_registry = {}
    
    # add employees from daily_data
    if daily_data and 'result' in daily_data:
        for record in daily_data['result']:
            emp_name = record['employee']
            
            if emp_name not in employee_registry:
                employee_registry[emp_name] = {
                    'employee_info': {
                        'name': emp_name,
                        'department': record.get('department', 'Unknown')
                    },
                    'daily_data': None,
                    'monthly_data': None
                }
    
    # add employees from monthly_data only if not already added from daily_data
    if monthly_data and 'result' in monthly_data:
        for record in monthly_data['result']:
            emp_name = record['employee']
            
            if emp_name not in employee_registry:
                employee_registry[emp_name] = {
                    'employee_info': {
                        'name': emp_name,
                        'department': record.get('department', 'Unknown')
                    },
                    'daily_data': None,
                    'monthly_data': None
                }
                
    return employee_registry


#adding daily_data to employee list
def add_daily_data_to_registry(employee_registry, daily_data):
    
    if not daily_data or 'result' not in daily_data:
        return employee_registry
    
    for record in daily_data['result']:
        emp_name = record['employee']
        
        if emp_name in employee_registry:
            employee_registry[emp_name]['daily_data'] = {
                'date': record['date'],
                'work_hours': record['work_time'],
                'entry_time': record['entry'],
                'exit_time': record['exit'],
            }
            
    return employee_registry


#adding monthly_data to employee list
def calculate_monthly_average(employee_data, working_days, is_current_month, first_day, last_day):
    
    employee_monthly_stats = defaultdict(lambda: {
        'total_work_hours': 0.0,
        'total_days_worked': 0,
        'department': None
    })

    # Aggregate monthly data
    for record in employee_data:
        emp_name = record['employee']
        employee_monthly_stats[emp_name]['total_work_hours'] += record['work_time']
        employee_monthly_stats[emp_name]['total_days_worked'] += 1
        
        if employee_monthly_stats[emp_name]['department'] is None:
            employee_monthly_stats[emp_name]['department'] = record['department']

    # result output with data
    result = []
    for emp_name, m_stats in employee_monthly_stats.items():
        avg_working_hours = 0
        if working_days > 0:
            avg_working_hours = round(m_stats['total_work_hours'] / working_days,2)  
            
        result.append({
            "employee": emp_name,
            'department': m_stats['department'],
            'monthly_average': avg_working_hours,
            "total_hours_worked": m_stats['total_work_hours'],
            'total_days_worked': m_stats['total_days_worked'],
            'total_monthly_working_days': working_days,
            'result_type': 'monthly_average'
        })

    return {"result": result, "total_count": len(result)}


#find monthly average for a date
def monthly_average(select_date):
    
    # if date is string convert to datetime object
    if isinstance(select_date, str):
        try:
            select_date = datetime.strptime(select_date, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date format. Use YYYY-MM-DD."}

    # Extract year and month
    year = select_date.year
    month = select_date.month
    today_date = date.today()

    # Calculate date range
    first_day = date(year, month, 1)

    if year == today_date.year and month == today_date.month:
        last_day = today_date
        is_current_month = True
    elif select_date < today_date:
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        is_current_month = False
    else:
        return {"error": "Please enter a valid date"}

    # Calculate working days
    working_days = working_days_count(first_day, last_day)

    # Get checkin data for the month
    checkin_data = get_checkin_data(
        from_date=first_day.strftime('%Y-%m-%d'), 
        to_date=last_day.strftime('%Y-%m-%d')
    )

    if isinstance(checkin_data, dict) and ('error' in checkin_data or 'message' in checkin_data):
        return checkin_data

    # Process data
    grouped_data = sort_checkin_data(checkin_data)
    processed_data = calculate_work_hours(grouped_data)
    monthly_stats = calculate_monthly_average(
        processed_data['result'],
        working_days,
        is_current_month,
        first_day,
        last_day
    )

    return monthly_stats


#add monthly data to employee list
def add_monthly_data_to_registry(employee_registry, monthly_data):
    
    if not monthly_data or 'result' not in monthly_data:
        return employee_registry
        
    for record in monthly_data['result']:
        emp_name = record['employee']
        
        if emp_name in employee_registry:
            employee_registry[emp_name]['monthly_data'] = {
                'average_work_hours': record['monthly_average'],
                'total_hours_worked': record['total_hours_worked'],
                'total_days_worked': record['total_days_worked'],
                'total_working_days': record['total_monthly_working_days']
            }
            
    return employee_registry


#main function
@frappe.whitelist(allow_guest=True)
def fetch_checkins(from_date=None, to_date=None, specific_date=None):
    
    try:
        # Validate input parameters
        if from_date and not to_date:
            frappe.throw("Provide To date")
        if to_date and not from_date:
            frappe.throw("Provide From date")
        if (from_date and specific_date) or (to_date and specific_date):
            frappe.throw("Provide either Date range or a single date")

        # Set date as today if no date provided
        if specific_date is None and from_date is None and to_date is None:
            specific_date = today()

        # find data if date range is provided
        if from_date and to_date:
            emp_data = get_checkin_data(from_date, to_date, None)
            
            if isinstance(emp_data, dict) and ('error' in emp_data or 'message' in emp_data):
                return emp_data
                
            grouped_data = sort_checkin_data(emp_data)
            result = calculate_work_hours(grouped_data)
            return checkin_data_for_date_range(result)

        # find data if specific_date or today is selected
        elif specific_date:
            # Get daily data
            daily_emp_data = get_checkin_data(None, None, specific_date)
            
            daily_result = None
            if not (isinstance(daily_emp_data, dict) and ('error' in daily_emp_data or 'message' in daily_emp_data)):
                grouped_daily = sort_checkin_data(daily_emp_data)
                daily_result = calculate_work_hours(grouped_daily)
            
            # Get monthly data
            monthly_result = monthly_average(specific_date)
            
            # creating employee list and combining monthly and daily data
            employee_registry = create_employee_registry(daily_result, monthly_result)
            employee_registry = add_daily_data_to_registry(employee_registry, daily_result)
            employee_registry = add_monthly_data_to_registry(employee_registry, monthly_result)
            
            return {"employees": employee_registry,"length":len(employee_registry)}

    except Exception as e:
        frappe.log_error(str(e), "Unexpected error in fetch_checkins")
        return {"error": "Unexpected error occurred."}