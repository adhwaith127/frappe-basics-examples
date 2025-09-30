import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional, Set


# Get employee holidays
def _get_employee_holidays(start_date, end_date):
    try:
        holiday_query = """
            SELECT em.name as employee, em.holiday_list, h.holiday_date
            FROM `tabEmployee` em 
            LEFT JOIN `tabHoliday` h ON em.holiday_list=h.parent
                AND h.holiday_date BETWEEN %s AND %s
            WHERE em.status='Active'
            ORDER BY em.name
        """
        employee_holiday_data = frappe.db.sql(holiday_query, (start_date, end_date), as_dict=True)

        employee_holidays = defaultdict(list)

        for holiday in employee_holiday_data:
            emp = holiday['employee']
            if holiday['holiday_date']:
                employee_holidays[emp].append(getdate(holiday['holiday_date']))
            else:
                # ensures employees with no holiday list or no holidays in range exist in dict
                employee_holidays.setdefault(emp, [])

        employee_holidays = dict(employee_holidays)
        
        return employee_holidays

    except Exception as e:
        frappe.log_error("Error in getting employee holidays", str(e))
        return {}


# Get all approved leaves for all employees in the date range
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


# Get employee checkin data
def _get_employee_data(start_date, end_date):
    try:
        query = """
                SELECT
                    ec.employee, ec.time, ec.log_type,
                    em.name, em.department, em.reports_to, em.default_shift, em.holiday_list, em.image,
                    st.end_time
                FROM `tabEmployee Checkin` AS ec
                JOIN `tabEmployee` AS em ON ec.employee = em.name
                LEFT JOIN `tabShift Type` AS st on em.default_shift=st.name
                WHERE DATE(ec.time) BETWEEN %s and %s AND em.status = 'Active'
                ORDER BY ec.employee, ec.time
            """
        raw_checkin_data = frappe.db.sql(query, (start_date, end_date), as_dict=True)
        
        return raw_checkin_data
    
    except Exception as e:
        frappe.log_error("Error in employee data sql", str(e))
        return []


# Filter checkins to exclude holidays
def _filter_checkins(raw_checkin_data, employee_holidays):
    try:
        filtered_data = []
        for record in raw_checkin_data:
            employee = record['employee']
            checkin_date = getdate(record['time'])
            if checkin_date not in employee_holidays.get(employee, []):
                filtered_data.append(record) 

        return filtered_data
    
    except Exception as e:
        frappe.log_error("Error in filtering checkins", str(e))
        return []


# Calculate work hours
def _calculate_employee_work_hours(logs, shift_end=None):
    try:
        if not logs:
            return {
                "employee": None, 
                "department": None, 
                "reports_to": None,
                "date": None, 
                "daily_working_hours": 0.0, 
                "entry_time": None, 
                "exit_time": None, 
                "checkin_pairs": []
            }

        total_working_hours = 0.0
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
                    session_duration = time_diff_in_hours(log['time'], last_in_time)
                    total_working_hours += session_duration
                    checkin_pairs.append({
                        "in_time": last_in_time.strftime("%H:%M"),
                        "out_time": log['time'].strftime("%H:%M"),
                        "duration": round(session_duration, 2)
                    })
                    last_in_time = None

                last_out_time = log['time']
        
        if last_in_time and shift_end and last_in_time.date()<date.today():
            session_duration = time_diff_in_hours(shift_end, last_in_time)
            total_working_hours += session_duration
            checkin_pairs.append({
                "in_time": last_in_time.strftime("%H:%M"),
                "out_time": shift_end.strftime("%H:%M"),
                "duration": round(session_duration, 2)
            })

        if last_in_time and shift_end is None:
            checkin_pairs.append({
                "in_time": last_in_time.strftime("%H:%M"),
                "out_time": last_in_time.strftime("%H:%M"),
                "duration": 0.0
            })

        return {
            "employee": logs[0]['employee'],
            "department": logs[0]['department'],
            "reports_to": logs[0]['reports_to'],
            "image": logs[0].get('image'),
            "date": format_datetime(logs[0]['time'], 'yyyy-MM-dd'),
            "daily_working_hours": round(total_working_hours, 2),
            "entry_time": first_in_time.strftime("%H:%M") if first_in_time else None,
            "exit_time": last_out_time.strftime("%H:%M") if last_out_time else None,
            "checkin_pairs": checkin_pairs
        }

    except Exception as e:
        frappe.log_error("Error in calculating employee work hours", str(e)) 
        return {"error": "Error in calculating work hours"}


# Sort and process checkin data
def _sort_checkin_data(filtered_checkin_data):
    try:
        grouped_emp_data = defaultdict(lambda: defaultdict(list))
        for entry in filtered_checkin_data:
            date_str = format_datetime(entry['time'], 'yyyy-MM-dd')
            grouped_emp_data[entry['employee']][date_str].append(entry)

        daily_summaries = []
        for employee, day in grouped_emp_data.items():
            for date, logs in day.items():
                if logs:
                    shift_end = logs[0]['end_time']
                    if isinstance(shift_end, timedelta):
                        # Convert timedelta to a time object
                        shift_end = (datetime.min + shift_end).time()
                    elif isinstance(shift_end, str):   # check whether shift end is string 
                        shift_end = datetime.strptime(shift_end, "%H:%M").time()
                    elif isinstance(shift_end, datetime):  # or datetime then convert
                        shift_end = shift_end.time() 
                    
                    # getdate gets the date part from datetime and combine with end time(str/time obj) for datetime
                    if shift_end:
                        shift_end = datetime.combine(getdate(logs[0]['time']), shift_end)
                    daily_summary = _calculate_employee_work_hours(logs, shift_end)
                    daily_summaries.append(daily_summary)

        return daily_summaries

    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return []


# Calculate working days using weekly logic
def _calculate_working_days_weekly(employee_holidays):
    holiday_count = len(employee_holidays)
    working_days = 5 - holiday_count  # 5 weekdays minus holidays
    return max(working_days, 0)


# Enhanced daily work hours calculation with status and leave handling
def _calculate_daily_work_hours_with_status(logs, employee_holidays, employee_leaves, date_str, employee_info):
    
    if not logs:
        if not employee_info:
            return None
            
        if date_str in [str(d) for d in employee_holidays]:
            status = "Holiday"
        elif date_str in employee_leaves:
            status = "On Leave" 
        else:
            status = "Absent"
            
        return {
            "employee": employee_info['name'],
            "department": employee_info['department'], 
            "reports_to": employee_info['reports_to'],
            "image": employee_info.get('image'),
            "date": date_str,
            "daily_working_hours": 0.0,
            "entry_time": None,
            "exit_time": None, 
            "status": status,
            "checkin_pairs": []
        }

    # Process logs using existing logic
    work_summary = _calculate_employee_work_hours(logs)
    
    # Determine status
    if date_str in employee_leaves:
        status = "On Leave"
    elif date_str in [str(d) for d in employee_holidays]:
        status = "Holiday"
    elif work_summary.get('entry') or work_summary.get('exit'):
        status = "Present"
    else:
        status = "Absent"

    # Merge both formats for compatibility
    return {
        "employee": work_summary['employee'],
        "department": work_summary['department'],
        "reports_to": work_summary['reports_to'], 
        "image": work_summary.get('image'),
        "date": work_summary['date'],
        "work_time": work_summary['daily_working_hours'],
        "daily_working_hours": work_summary['daily_working_hours'],
        "entry_time": work_summary['entry'],
        "exit_time": work_summary['exit'],
        "entry": work_summary['entry'],
        "exit": work_summary['exit'],
        "status": status,
        "checkin_pairs": work_summary['checkin_pairs']
    }


# Process checkin data with holiday and leave integration using weekly/monthly logic
def _get_processed_checkin_data(from_date: date, to_date: date) -> List[Dict[str, Any]]:
    try:
        if not from_date or not to_date:
            return []
        
        # Get data
        raw_checkin_data = _get_employee_data(from_date, to_date)
        if not raw_checkin_data:
            return []
        
        # Get holidays
        employee_holidays = _get_employee_holidays(from_date, to_date)
        if not employee_holidays:
            frappe.log_error("No holiday data found", "Holiday mapping is empty")

        # Get leaves data
        employee_leaves = _get_leaves_for_period(from_date, to_date)

        # Filter checkins to exclude holidays
        filtered_checkin_data = _filter_checkins(raw_checkin_data, employee_holidays)

        # Process daily summaries
        daily_summaries = _sort_checkin_data(filtered_checkin_data)
        if not daily_summaries:
            return []

        # Get all employees that had checkin data
        employees_with_data = set()
        for summary in daily_summaries:
            employees_with_data.add(summary['employee'])

        # Add status and leave information
        enhanced_summaries = []
        for summary in daily_summaries:
            emp_holidays = employee_holidays.get(summary['employee'], [])
            emp_leaves = employee_leaves.get(summary['employee'], set())
            
            # Create employee info for compatibility
            employee_info = {
                'name': summary['employee'],
                'department': summary['department'],
                'reports_to': summary['reports_to'],
                'image': summary.get('image')
            }
            
            # Get enhanced summary with status
            enhanced = _calculate_daily_work_hours_with_status(
                None,  # We already processed the logs
                emp_holidays,
                emp_leaves,
                summary['date'],
                employee_info
            )
            
            # Merge with existing summary data
            enhanced.update({
                'work_time': summary['daily_working_hours'],
                'daily_working_hours': summary['daily_working_hours'],
                'entry_time': summary['entry_time'],
                'exit_time': summary['exit_time'],
                'entry': summary['entry_time'],
                'exit': summary['exit_time'],
                'checkin_pairs': summary['checkin_pairs'],
                'status': 'Present'  # Since they had checkin data
            })
            
            enhanced_summaries.append(enhanced)

        return enhanced_summaries
        
    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return []


# Create period summary from daily records with individual working days
def _create_summary_with_working_days(daily_records: List[Dict[str, Any]], period_type: str, start_date: date, end_date: date, employee_holidays: dict) -> List[Dict[str, Any]]:

    start_date = getdate(start_date)
    end_date = getdate(end_date)
    today_date = getdate(today())

    employee_stats = defaultdict(lambda: {
        'total_work_hours': 0.0, 
        'days_worked': 0, 
        'department': None, 
        'reports_to': None,
        'image': None
    })

    for record in daily_records:
        emp = employee_stats[record['employee']]
        emp['total_work_hours'] += record.get('daily_working_hours', 0.0)
        emp['days_worked'] += 1
        if not emp['department']: emp['department'] = record.get('department')
        if not emp['reports_to']: emp['reports_to'] = record.get('reports_to')
        if not emp['image']: emp['image'] = record.get('image')
    
    result = []
    for emp_name, stats in employee_stats.items():
        # Get all holidays for the employee
        all_emp_holidays = employee_holidays.get(emp_name, [])
        
        # Filter holidays to only include those within the specific period
        holidays_in_period = [h for h in all_emp_holidays if start_date <= h <= end_date]

        # Calculate working days based on period type using the correctly filtered holiday list
        if period_type == 'monthly':
            is_current_month = (start_date.year == today_date.year and 
                              start_date.month == today_date.month)
            
            if is_current_month and end_date >= today_date:
                # Current month: calculate days from start to today
                actual_start_date = start_date
                actual_end_date = min(end_date, today_date)
            else:
                # Past month: use provided boundaries
                actual_start_date = start_date
                if end_date.month == start_date.month:
                    actual_end_date = end_date
                else:
                    # Full month case
                    _, days_in_month = calendar.monthrange(start_date.year, start_date.month)
                    actual_end_date = start_date.replace(day=days_in_month)
            
            # NOW calculate holidays with ACTUAL boundaries
            holidays_in_period = [h for h in all_emp_holidays 
                                if actual_start_date <= h <= actual_end_date]
            
            num_days_in_period = (actual_end_date - actual_start_date).days + 1
            total_working_days = num_days_in_period - len(holidays_in_period)

        elif period_type == 'weekly':
            is_current_week = (start_date <= today_date <= end_date)
            
            if is_current_week:
                # Current week: calculate from start_date to today
                actual_start_date = start_date
                actual_end_date = min(end_date, today_date)
                
                # Calculate holidays with actual boundaries
                holidays_in_period = [h for h in all_emp_holidays 
                                    if actual_start_date <= h <= actual_end_date]
                
                # For current week: actual days minus holidays
                num_days_in_period = (actual_end_date - actual_start_date).days + 1
                total_working_days = num_days_in_period - len(holidays_in_period)
            else:
                # Past week: use 5-day work week logic
                holidays_in_period = [h for h in all_emp_holidays 
                                    if start_date <= h <= end_date]
                total_working_days = _calculate_working_days_weekly(holidays_in_period)
        else:
            # Daily
            holidays_in_period = [h for h in all_emp_holidays if h == start_date]
            total_working_days = 1 - len(holidays_in_period)
            
        total_working_days = max(total_working_days, 0)
        avg_hours = round(stats['total_work_hours'] / total_working_days, 2) if total_working_days > 0 else 0
        
        result.append({
            "employee": emp_name, 
            "average_work_hours": avg_hours, 
            "total_hours_worked": round(stats['total_work_hours'], 2),
            "total_days_worked": stats['days_worked'],
            "total_working_days_in_period": total_working_days,
            "employee_working_days": total_working_days,
            "holidays_in_period": len(holidays_in_period)
        })
    return result


# Add data to employee registry
def _add_to_registry(registry: Dict, data: List[Dict], data_key: str):
    for record in data:
        emp_name = record.get('employee')
        if emp_name and emp_name in registry:
            clean_record = record.copy()
            # Remove duplicate employee info since it's already in employee_info
            for field in ['employee', 'department', 'reports_to', 'image']:
                clean_record.pop(field, None)
            registry[emp_name][data_key] = clean_record


# manager to subordinates mapping
def _get_hierarchy_map() -> defaultdict[str, List[str]]:
    try:
        employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "reports_to"])
        hierarchy = defaultdict(list)
        for emp in employees:
            if emp.reports_to:
                hierarchy[emp.reports_to].append(emp.name)
        return hierarchy
    except Exception as e:
        frappe.log_error("Error in hierarchy mapping", str(e))
        return defaultdict(list)


# Get all subordinates using breadth-first search
def _get_all_subordinates(manager_id: str, hierarchy_map: Dict) -> List[str]:
    if not manager_id or not hierarchy_map:
        return []

    all_subordinates = set()
    queue = hierarchy_map.get(manager_id, [])
    visited = set(queue)
    all_subordinates.update(queue)

    while queue:
        current = queue.pop(0)
        direct_reports = hierarchy_map.get(current, [])
        for report in direct_reports:
            if report not in visited:
                visited.add(report)
                all_subordinates.add(report)
                queue.append(report)
    
    return list(all_subordinates)


# Calculate week/month boundaries for given date
def _get_date_boundaries(target_date: date) -> Dict[str, date]:
    today_date = getdate(today())
    
    month_start = target_date.replace(day=1)
    _, days_in_month = calendar.monthrange(target_date.year, target_date.month)
    
    # If target date is today or in the past, use actual month end, otherwise cap at today
    if target_date <= today_date:
        month_end = target_date.replace(day=days_in_month)
        if month_end > today_date:
            month_end = today_date
    else:
        month_end = target_date.replace(day=days_in_month)
    
    week_start = target_date - timedelta(days=target_date.weekday())
    week_end_calc = week_start + timedelta(days=4)
    
    # Similar logic for week
    if target_date <= today_date:
        week_end = min(week_end_calc, today_date)
    else:
        week_end = week_end_calc

    return {
        "month_start": month_start,
        "month_end": month_end,
        "week_start": week_start,
        "week_end": week_end,
        "earliest_date": min(month_start, week_start),
        "latest_date": max(month_end, week_end)
    }


# Create employee registry structure
def _build_employee_registry(employees: List[Dict]) -> Dict:
    return {
        emp.name: {
            'employee_info': {
                'name': emp.name, 
                'department': emp.department, 
                'reports_to': emp.reports_to,
                'image': emp.image
            },
            'daily_data': {}, 
            'weekly_summary': {}, 
            'monthly_summary': {}
        } for emp in employees
    }


# Process and categorize data into daily/weekly/monthly periods
def _process_data_by_periods(registry: Dict, all_data: List[Dict], boundaries: Dict, target_date: date, employee_holidays: dict):
    if not all_data:
        # If there's no check-in data at all, we still need to process absences/holidays
        pass

    daily_data, weekly_data, monthly_data = [], [], []
    
    for record in all_data:
        record_date = getdate(record['date'])
        
        if boundaries['month_start'] <= record_date <= boundaries['month_end']:
            monthly_data.append(record)
        if boundaries['week_start'] <= record_date <= boundaries['week_end']:
            weekly_data.append(record)
        if record_date == target_date:
            daily_data.append(record)

    if daily_data:
        _add_to_registry(registry, daily_data, 'daily_data')

    if weekly_data:
        summary = _create_summary_with_working_days(weekly_data, 'weekly', boundaries['week_start'], boundaries['week_end'], employee_holidays)
        _add_to_registry(registry, summary, 'weekly_summary')

    if monthly_data:
        summary = _create_summary_with_working_days(monthly_data, 'monthly', boundaries['month_start'], boundaries['month_end'], employee_holidays)
        _add_to_registry(registry, summary, 'monthly_summary')
    
    # Ensure all employees have daily, weekly, and monthly records ---
    target_date_str = format_datetime(target_date, 'yyyy-MM-dd')
    today_date = getdate(today())
    
    for emp_name, emp_data in registry.items():
        emp_holidays = employee_holidays.get(emp_name, [])

        # 1. Daily data (existing logic is fine)
        if not emp_data['daily_data']:
            status = "Holiday" if target_date in emp_holidays else "Absent"
            emp_data['daily_data'] = {
                'date': target_date_str, 'daily_working_hours': 0.0, 
                'entry_time': None, 'exit_time': None, 'status': status, 'checkin_pairs': []
            }

        # 2. Weekly summary with proper boundary-matched holidays
        if not emp_data['weekly_summary']:
            is_current_week = boundaries['week_start'] <= today_date <= boundaries['week_end']
            
            if is_current_week:
                actual_week_end = min(boundaries['week_end'], today_date)
                holidays_in_week = [h for h in emp_holidays 
                                  if boundaries['week_start'] <= h <= actual_week_end]
                working_days = ((actual_week_end - boundaries['week_start']).days + 1) - len(holidays_in_week)
            else:
                holidays_in_week = [h for h in emp_holidays 
                                  if boundaries['week_start'] <= h <= boundaries['week_end']]
                working_days = _calculate_working_days_weekly(holidays_in_week)
            
            emp_data['weekly_summary'] = {
                "average_work_hours": 0.0, "total_hours_worked": 0.0, "total_days_worked": 0,
                "total_working_days_in_period": max(working_days, 0),
                "employee_working_days": max(working_days, 0),
                "holidays_in_period": len(holidays_in_week)
            }

        # 3. Monthly summary with proper boundary-matched holidays  
        if not emp_data['monthly_summary']:
            is_current_month = (boundaries['month_start'].year == today_date.year and 
                               boundaries['month_start'].month == today_date.month)
            
            if is_current_month:
                actual_month_end = min(boundaries['month_end'], today_date)
                holidays_in_month = [h for h in emp_holidays 
                                   if boundaries['month_start'] <= h <= actual_month_end]
                working_days = ((actual_month_end - boundaries['month_start']).days + 1) - len(holidays_in_month)
            else:
                holidays_in_month = [h for h in emp_holidays 
                                   if boundaries['month_start'] <= h <= boundaries['month_end']]
                _, days_in_month = calendar.monthrange(boundaries['month_start'].year, boundaries['month_start'].month)
                working_days = days_in_month - len(holidays_in_month)
            
            emp_data['monthly_summary'] = {
                "average_work_hours": 0.0, "total_hours_worked": 0.0, "total_days_worked": 0,
                "total_working_days_in_period": max(working_days, 0),
                "employee_working_days": max(working_days, 0),
                "holidays_in_period": len(holidays_in_month)
            }



# Structure final response with hierarchy
def _create_hierarchy_response(registry: Dict, manager_id: str, subordinate_ids: List[str]) -> Dict[str, Any]:
    manager_data = registry.get(manager_id, {})
    subordinates_data = {emp_id: data for emp_id, data in registry.items() if emp_id in subordinate_ids}
    
    return {
        "user_id": manager_id,
        "manager_data": manager_data,
        "subordinates_data": subordinates_data,
        "total_count": len(subordinates_data) + (1 if manager_data else 0)
    }


# Main API endpoint for fetching checkin data using weekly/monthly logic
@frappe.whitelist()
def fetch_checkins(from_date: str = None, to_date: str = None, specific_date: str = None) -> Dict[str, Any]:
    try:
        # Input validation
        if from_date and not to_date: 
            frappe.throw("Please provide 'to_date' for date range.")
        if to_date and not from_date: 
            frappe.throw("Please provide 'from_date' for date range.")
        if (from_date or to_date) and specific_date: 
            frappe.throw("Provide either date range or specific date, not both.")
        
        if not any([from_date, to_date, specific_date]):
            specific_date = today()

        # Determine user hierarchy
        if frappe.session.user == 'Administrator':
            manager_id = "Administrator"
            all_employees = frappe.get_all("Employee", 
                filters=[['status', '=', 'Active']], 
                fields=["name", 'department', 'reports_to', 'image']
            )
            subordinate_ids = [emp.name for emp in all_employees if emp.name != manager_id]
        else:
            manager_id = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
            if not manager_id:
                frappe.throw("User not linked to active employee record.")
            
            hierarchy_map = _get_hierarchy_map()
            subordinate_ids = _get_all_subordinates(manager_id, hierarchy_map)
            allowed_employees = set(subordinate_ids + [manager_id])
            all_employees = frappe.get_all("Employee", 
                filters={"name": ["in", list(allowed_employees)]},
                fields=["name", "department", "reports_to", 'image']
            )

        # Build registry and process data
        registry = _build_employee_registry(all_employees)

        # Handle date range request
        if from_date and to_date:
            try:
                start_date, end_date = getdate(from_date), getdate(to_date)
                processed_data = _get_processed_checkin_data(start_date, end_date)
                
                if not processed_data:
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                boundaries = {"earliest_date": start_date, "latest_date": end_date}
                
                # Get holidays for working days calculation
                employee_holidays = _get_employee_holidays(start_date, end_date)
                
                all_data = _get_processed_checkin_data(start_date, end_date)
                _process_data_by_periods(registry, all_data, boundaries, target_date, employee_holidays)
                
                return _create_hierarchy_response(registry, manager_id, subordinate_ids)
                    
                # return _create_summary_with_working_days(processed_data, period_type, start_date, end_date, employee_holidays)
                
            except Exception as e:
                frappe.log_error("Error in date range processing", str(e))
                return {"error": "Failed to process date range request"}

        # Handle specific date request
        elif specific_date:
            try:
                target_date = getdate(specific_date)
                if target_date > getdate(today()):
                    return {"error": "Cannot fetch data for future date."}
                
                boundaries = _get_date_boundaries(target_date)
                
                # Get holidays for the entire period
                employee_holidays = _get_employee_holidays(boundaries['earliest_date'], boundaries['latest_date'])
                
                all_data = _get_processed_checkin_data(boundaries['earliest_date'], boundaries['latest_date'])
                _process_data_by_periods(registry, all_data, boundaries, target_date, employee_holidays)
                
                return _create_hierarchy_response(registry, manager_id, subordinate_ids)
                
            except Exception as e:
                frappe.log_error("Error in specific date processing", str(e))
                return {"error": "Failed to process request"}

    except Exception as e:
        frappe.log_error("Error in main function", str(e))
        return {"error": "System error occurred."}