import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional


# helper function to build employee info
def _build_employee_info(record):
    try:
        employee_info = {
                    'name': record['employee'],
                    'emp_display_name': record.get('emp_display_name'),
                    'department': record['department'],
                    'reports_to': record['reports_to'],
                    'image': record.get('image')
                }
        return employee_info
    
    except Exception as e:
        frappe.log_error("Error in build employee info",str(e))
        return {}


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
                employee_holidays.setdefault(emp, [])

        employee_holidays = dict(employee_holidays)
        
        return employee_holidays

    except Exception as e:
        frappe.log_error("Error in getting employee holidays", str(e))
        return {}


# Get all approved leaves for all employees in the date range (including half-day info)
def _get_leaves_for_period(start_date, end_date):
    try:
        leaves_query = """
            SELECT employee, from_date, to_date, half_day, half_day_date, leave_type
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

        employee_leaves = defaultdict(dict)

        for leave in leaves_data:
            leave_start = max(leave.from_date, start_date)
            leave_end = min(leave.to_date, end_date)

            current_date = leave_start
            while current_date <= leave_end:
                day_str = str(current_date)
                employee_leaves[leave.employee][day_str] = {
                    'leave_type': leave.leave_type,
                    'half_day': leave.half_day,
                    'half_day_date': str(leave.half_day_date) if leave.half_day_date else None,
                    'is_half_day_on_date': leave.half_day and str(leave.half_day_date) == day_str if leave.half_day_date else False
                }
                current_date += timedelta(days=1)
        
        return dict(employee_leaves)

    except Exception as e:
        frappe.log_error("Error in getting employee leaves", str(e))
        return {}
    

# Get employee checkin data with shift info (NO LONGER FILTERS HOLIDAYS)
def _get_employee_data(start_date, end_date):
    try:
        query = """
                SELECT
                    ec.employee, ec.time, ec.log_type,
                    em.name,em.employee_name as emp_display_name, em.department, em.reports_to, em.default_shift, em.holiday_list, em.image,
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


# UPDATED: Working day classification logic based on leave_calc.py with leave type considerations
def _classify_day(has_checkin, has_leave, has_holiday, leave_info=None):
    """
    Classify a day based on checkin, leave, and holiday status
    Returns the classification type for the day
    """
    # Priority 1: Leave + Holiday = Holiday (leave takes priority)
    if has_leave and has_holiday:
        if leave_info and leave_info.get('is_half_day_on_date'):
            return "half_day_leave_holiday"  # New classification for half-day leave on holiday
        else:
            # Check leave type for full day leave on holiday
            leave_type = leave_info.get('leave_type', '').upper() if leave_info else ''
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                return "lwp_leave_holiday"  # Leave without pay on holiday
            else:
                return "holiday_priority"  # Regular leave on holiday
    
    # Priority 2: Leave handling (with or without checkin)
    if has_leave:
        leave_type = leave_info.get('leave_type', '').upper() if leave_info else ''
        
        if leave_info and leave_info.get('is_half_day_on_date'):
            # Half day leave - check leave type
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                return "half_day_lwp_leave"
            else:
                return "half_day_leave"  # Regular half day leave
        else:
            # Full day leave
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                if has_checkin:
                    return "lwp_leave_override"  # LWP but employee worked = override, count as 1.0
                else:
                    return "lwp_leave"  # LWP leave, should count as working day (1.0) but not present
            else:
                # Regular leave (Casual, Annual, etc.)
                if has_checkin:
                    return "full_day_leave_override"  # Regular leave but employee worked = override, count as 1.0
                else:
                    return "full_day_leave"  # Regular leave, no work = subtract from total days (0.0)
    
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


def _get_working_day_value(day_classification):
    """
    Get the working day value based on day classification
    UPDATED: Added leave type considerations
    """
    if day_classification == "full_day_leave":
        return 0.0  # Regular leave subtracts from working days
    elif day_classification in ["half_day_leave", "half_day_leave_holiday"]:
        return 0.5  # Regular half day leave counts as 0.5
    elif day_classification == "half_day_lwp_leave":
        return 0.5  # LWP half day still counts as 0.5 working day
    elif day_classification in ["lwp_leave", "lwp_leave_holiday"]:
        return 1.0  # LWP counts as working day (but employee not present)
    elif day_classification in ["full_day_leave_override", "lwp_leave_override"]:
        return 1.0  # Employee worked despite leave = override, count as full day
    elif day_classification in ["working_holiday", "regular_working"]:
        return 1.0
    else:  # holiday_priority, non_working_holiday, regular_non_working
        return 0.0


# UPDATED: Calculate employee working days with proper logic including leave types
def _calculate_employee_working_days_new(employee_name, start_date, end_date, checkin_dates, employee_holidays, employee_leaves):
    """
    Calculate working days for an employee using the new logic with leave type considerations
    """
    try:
        total_working_days = 0.0
        emp_holidays = employee_holidays.get(employee_name, [])
        emp_leaves = employee_leaves.get(employee_name, {})
        
        current_date = start_date
        while current_date <= end_date:
            day_str = str(current_date)
            
            # Check conditions for this day
            has_checkin = day_str in checkin_dates
            has_leave = day_str in emp_leaves
            has_holiday = current_date in emp_holidays
            
            # Get leave info if exists
            leave_info = emp_leaves.get(day_str) if has_leave else None
            
            # Classify the day
            day_classification = _classify_day(has_checkin, has_leave, has_holiday, leave_info)
            
            # Get working day value
            working_day_value = _get_working_day_value(day_classification)
            
            total_working_days += working_day_value
            current_date += timedelta(days=1)
        
        return total_working_days
        
    except Exception as e:
        frappe.log_error("Error in calculating employee working days", str(e))
        return 0.0


# Calculate work hours (unchanged)
def _calculate_employee_work_hours(logs):
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
        current_time = datetime.now()
        current_date = date.today()
        log_date = logs[0]['time'].date()

        default_close_time = datetime.combine(log_date, datetime.strptime("23:59", "%H:%M").time())

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
        
        if last_in_time:
            if log_date<current_date:
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": last_in_time.strftime("%H:%M"),
                    "duration": 0.0,
                    "ongoing": False
                })
                last_out_time = last_in_time

            elif log_date==current_date:
                checkin_pairs.append({
                "in_time": last_in_time.strftime("%H:%M"),
                "out_time": "Ongoing",
                "duration": 0.0,
                "ongoing": True
            })
            last_out_time = None
                                                    
        return {
            "employee": logs[0]['employee'],
            "emp_display_name": logs[0].get('emp_display_name'),
            "department": logs[0]['department'],
            "reports_to": logs[0]['reports_to'],
            "image": logs[0].get('image'),
            "date": format_datetime(logs[0]['time'], 'yyyy-MM-dd'),
            "daily_working_hours": round(total_working_hours, 2),
            "entry_time": first_in_time.strftime("%H:%M") if first_in_time else None,
            "exit_time": last_out_time.strftime("%H:%M") if last_out_time else None,
            "checkin_pairs": checkin_pairs,
            'has_ongoing_session': last_in_time and (log_date == current_date) 
        }

    except Exception as e:
        frappe.log_error("Error in calculating employee work hours", str(e)) 
        return {"error": "Error in calculating work hours"}


# Sort and process checkin data (unchanged)
def _sort_checkin_data(raw_checkin_data):
    try:
        grouped_emp_data = defaultdict(lambda: defaultdict(list))
        for entry in raw_checkin_data:
            date_str = format_datetime(entry['time'], 'yyyy-MM-dd')
            grouped_emp_data[entry['employee']][date_str].append(entry)

        daily_summaries = []
        for employee, day in grouped_emp_data.items():
            for date, logs in day.items():
                if logs:
                    daily_summary = _calculate_employee_work_hours(logs)
                    daily_summaries.append(daily_summary)

        return daily_summaries

    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return []


# UPDATED: daily work hours calculation with NEW status logic including leave types
def _calculate_daily_work_hours_with_status(logs, employee_holidays, employee_leaves, date_str, employee_info):
    
    if not logs:
        if not employee_info:
            return None
            
        check_date = getdate(date_str)
        emp_name = employee_info['name']
        
        # Check conditions
        has_leave = date_str in employee_leaves.get(emp_name, {})
        has_holiday = check_date in employee_holidays.get(emp_name, [])
        
        # Get leave info
        leave_info = employee_leaves.get(emp_name, {}).get(date_str) if has_leave else None
        
        # Classify day
        day_classification = _classify_day(False, has_leave, has_holiday, leave_info)
        
        # Determine status for display based on classification
        if day_classification == "full_day_leave":
            status = "On Leave"
        elif day_classification == "half_day_leave":
            status = "On Leave (Half Day)"
        elif day_classification == "lwp_leave":
            status = "Leave Without Pay"
        elif day_classification == "half_day_lwp_leave":
            status = "Leave Without Pay (Half Day)"
        elif day_classification in ["holiday_priority", "non_working_holiday", "lwp_leave_holiday"]:
            status = "Holiday"
        else:
            status = "Absent"

        return {
            "employee": employee_info['name'],
            "emp_display_name": employee_info.get('emp_display_name'),
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

    work_summary = _calculate_employee_work_hours(logs)
    
    # For employees with checkins, determine status based on leave and holiday conditions
    check_date = getdate(date_str)
    emp_name = work_summary['employee']
    has_holiday = check_date in employee_holidays.get(emp_name, [])
    has_leave = date_str in employee_leaves.get(emp_name, {})
    
    # Prioritize leave status and consider leave types
    if has_leave and has_holiday:
        leave_info = employee_leaves.get(emp_name, {}).get(date_str)
        leave_type = leave_info.get('leave_type', '').upper() if leave_info else ''
        
        if leave_info and leave_info.get('is_half_day_on_date'):
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                status = "Leave Without Pay (Half Day - Holiday Work)"
            else:
                status = "Half Day Leave (Holiday Work)"
        else:
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                status = "Present (LWP Override + Holiday Work)"
            else:
                status = "Present (Leave Override + Holiday Work)"
    elif has_leave:
        leave_info = employee_leaves.get(emp_name, {}).get(date_str)
        leave_type = leave_info.get('leave_type', '').upper() if leave_info else ''
        
        if leave_info and leave_info.get('is_half_day_on_date'):
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                status = "Leave Without Pay (Half Day)"  # Changed: LWP half day shows as LWP, not Present
            else:
                status = "Half Day Leave"  # Changed: Regular half day shows as Half Day Leave, not Present
        else:
            if 'WITHOUT PAY' in leave_type or 'LWP' in leave_type:
                status = "Present (LWP Override)"  # Full day LWP but worked = override
            else:
                status = "Present (Leave Override)"  # Full day leave but worked = override
    elif has_holiday:
        status = "Present (Holiday Work)"
    else:
        status = "Present"

    return {
        "employee": work_summary['employee'],
        "emp_display_name": work_summary.get('emp_display_name'),
        "department": work_summary['department'],
        "reports_to": work_summary['reports_to'], 
        "image": work_summary.get('image'),
        "date": work_summary['date'],
        "work_time": work_summary['daily_working_hours'],
        "daily_working_hours": work_summary['daily_working_hours'],
        "entry_time": work_summary['entry_time'],
        "exit_time": work_summary['exit_time'],
        "entry": work_summary['entry_time'],
        "exit": work_summary['exit_time'],
        "status": status,
        "checkin_pairs": work_summary['checkin_pairs']
    }


# Calculate effective working days using NEW logic with leave types
def _calculate_effective_working_days_new(start_date, end_date, emp_holidays, emp_leaves, checkin_dates, is_current_period=False):
    try:
        today_date = getdate(today())
        
        if is_current_period:
            actual_end_date = min(end_date, today_date - timedelta(days=1))
        else:
            actual_end_date = end_date
            
        if actual_end_date < start_date:
            return 0.0
            
        total_working_days = 0.0
        
        current_date = start_date
        while current_date <= actual_end_date:
            day_str = str(current_date)
            
            # Check conditions for this day
            has_checkin = day_str in checkin_dates
            has_leave = day_str in emp_leaves
            has_holiday = current_date in emp_holidays
            
            # Get leave info if exists
            leave_info = emp_leaves.get(day_str) if has_leave else None
            
            # Classify the day
            day_classification = _classify_day(has_checkin, has_leave, has_holiday, leave_info)
            
            # Get working day value
            working_day_value = _get_working_day_value(day_classification)
            
            total_working_days += working_day_value
            current_date += timedelta(days=1)

        return total_working_days
        
    except Exception as e:
        frappe.log_error("Error in calculating effective working days", str(e))
        return 0.0


# Process checkin data
def _get_processed_checkin_data(from_date, to_date):
    try:
        if not from_date or not to_date:
            return [], {}, {} 
               
        raw_checkin_data = _get_employee_data(from_date, to_date)
        if not raw_checkin_data:
            return [], {}, {} 
        
        employee_holidays = _get_employee_holidays(from_date, to_date)
        employee_leaves = _get_leaves_for_period(from_date, to_date)

        if not employee_holidays:
            frappe.log_error("No holiday data found", "Holiday mapping is empty")

        # NO LONGER FILTER OUT HOLIDAY CHECKINS
        daily_summaries = _sort_checkin_data(raw_checkin_data)
        if not daily_summaries:
            return [], employee_holidays, employee_leaves

        enhanced_summaries = []
        for summary in daily_summaries:
            emp_holidays = employee_holidays.get(summary['employee'], [])
            emp_leaves = employee_leaves.get(summary['employee'], {})
            
            employee_info = _build_employee_info(summary)
            
            enhanced = _calculate_daily_work_hours_with_status(
                None,
                employee_holidays,
                employee_leaves,
                summary['date'],
                employee_info
            )
            
            enhanced.update({
                'work_time': summary['daily_working_hours'],
                'daily_working_hours': summary['daily_working_hours'],
                'entry_time': summary['entry_time'],
                'exit_time': summary['exit_time'],
                'entry': summary['entry_time'],
                'exit': summary['exit_time'],
                'checkin_pairs': summary['checkin_pairs'],
                'status': 'Present'
            })
            
            enhanced_summaries.append(enhanced)

        return enhanced_summaries, employee_holidays, employee_leaves
        
    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return [], {}, {}  


# UPDATED: Create period summary with NEW effective working days calculation including leave types
def _create_summary_with_effective_working_days(daily_records, start_date, end_date, employee_holidays, employee_leaves, is_current_period=False):
    try:
        employee_stats = defaultdict(lambda: {
            'total_work_hours': 0.0, 
            'days_worked': 0, 
            'department': None, 
            'reports_to': None,
            'image': None,
            'emp_display_name': None,
            'checkin_dates': set()
        })

        for record in daily_records:
            emp = employee_stats[record['employee']]
            emp['total_work_hours'] += record.get('daily_working_hours', 0.0)
            emp['days_worked'] += 1
            emp['checkin_dates'].add(record['date'])
            if not emp['department']: emp['department'] = record.get('department')
            if not emp['reports_to']: emp['reports_to'] = record.get('reports_to')
            if not emp['image']: emp['image'] = record.get('image')
            if not emp['emp_display_name']: emp['emp_display_name'] = record.get('emp_display_name')
        
        result = []
        for emp_name, stats in employee_stats.items():
            emp_holidays = employee_holidays.get(emp_name, [])
            emp_leaves = employee_leaves.get(emp_name, {})

            # Use NEW effective working days calculation with leave type considerations
            effective_working_days = _calculate_effective_working_days_new(
                start_date, end_date, emp_holidays, emp_leaves, 
                stats['checkin_dates'], is_current_period
            )

            avg_hours = round(stats['total_work_hours'] / effective_working_days, 2) if effective_working_days > 0 else 0
            
            # Calculate period statistics for display
            emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves.keys()]
            holidays_in_period = [h for h in emp_holidays if start_date <= h <= end_date]
            leaves_in_period = [l for l in emp_leave_dates if start_date <= l <= end_date]
            
            result.append({
                "employee": emp_name, 
                "average_work_hours": avg_hours, 
                "total_hours_worked": round(stats['total_work_hours'], 2),
                "total_days_worked": stats['days_worked'],
                "effective_working_days": effective_working_days,
                "employee_working_days": effective_working_days,
                "holidays_in_period": len(holidays_in_period),
                "leaves_in_period": len(leaves_in_period)
            })
            
        return result
        
    except Exception as e:
        frappe.log_error("Error in creating summary with effective working days", str(e))
        return []


# Add data to employee registry (unchanged)
def _add_to_registry(registry, data, data_key):
    for record in data:
        emp_name = record.get('employee')
        if emp_name and emp_name in registry:
            clean_record = record.copy()
            for field in ['employee', 'department', 'reports_to', 'image']:
                clean_record.pop(field, None)
            registry[emp_name][data_key] = clean_record

# manager to subordinates mapping (unchanged)
def _get_hierarchy_map():
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


# Get all subordinates using bfs (unchanged)
def _get_all_subordinates(manager_id, hierarchy_map):
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


# Calculate week/month boundaries with current vs past (unchanged)
def _get_date_boundaries(target_date):
    try:
        today_date = getdate(today())
        yesterday = today_date - timedelta(days=1)
        
        month_start = target_date.replace(day=1)
        _, days_in_month = calendar.monthrange(target_date.year, target_date.month)
        month_end = target_date.replace(day=days_in_month)
        
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = week_start + timedelta(days=6)
        
        is_current_month = (target_date.year == today_date.year and target_date.month == today_date.month)
        is_current_week = (week_start <= today_date <= week_end)
        
        # For summary calculations (weekly/monthly averages)
        summary_month_end = yesterday if is_current_month else month_end
        summary_week_end = yesterday if is_current_week else week_end
        
        # For data fetching (includes today for daily data)
        fetch_month_end = month_end
        fetch_week_end = week_end

        return {
            "month_start": month_start,
            "month_end": fetch_month_end,           
            "week_start": week_start,
            "week_end": fetch_week_end,             
            "summary_month_end": summary_month_end, 
            "summary_week_end": summary_week_end,   
            "earliest_date": min(month_start, week_start),
            "latest_date": max(fetch_month_end, fetch_week_end),
            "is_current_month": is_current_month,
            "is_current_week": is_current_week
        }
        
    except Exception as e:
        frappe.log_error("Error in calculating date boundaries", str(e))
        return {}


# Create employee registry structure (unchanged)
def _build_employee_registry(employees):
    registry={}
    for emp in employees:
        registry[emp.name]={
            'employee_info': {
            'name': emp.name,
            'emp_display_name': emp.employee_name,
            'department': emp.department, 
            'reports_to': emp.reports_to,
            'image': emp.image
            },
            'daily_data': {}, 
            'weekly_summary': {}, 
            'monthly_summary': {}
        }
    return registry


# UPDATED: Process and categorize data into daily/weekly/monthly periods with NEW working days logic including leave types
def _process_data_by_periods(registry, all_data, boundaries, target_date, employee_holidays, employee_leaves):
    try:
        if not all_data:
            all_data = []

        daily_data, weekly_data, monthly_data = [], [], []
        
        for record in all_data:
            record_date = getdate(record['date'])
            
            # For monthly summary - use summary_month_end (excludes today)
            if boundaries['month_start'] <= record_date <= boundaries['summary_month_end']:
                monthly_data.append(record)
            
            # For weekly summary - use summary_week_end (excludes today)
            if boundaries['week_start'] <= record_date <= boundaries['summary_week_end']:
                weekly_data.append(record)
            
            # For daily data - use exact target date (includes today)
            if record_date == target_date:
                daily_data.append(record)

        # Process daily data (includes today)
        if daily_data:
            _add_to_registry(registry, daily_data, 'daily_data')

        # Process weekly summary (excludes today) - USING NEW LOGIC with leave types
        if weekly_data:
            summary = _create_summary_with_effective_working_days(
                weekly_data,  boundaries['week_start'], boundaries['summary_week_end'], 
                employee_holidays, employee_leaves, boundaries['is_current_week']
            )
            _add_to_registry(registry, summary, 'weekly_summary')

        # Process monthly summary (excludes today) - USING NEW LOGIC with leave types
        if monthly_data:
            summary = _create_summary_with_effective_working_days(
                monthly_data, boundaries['month_start'], boundaries['summary_month_end'], 
                employee_holidays, employee_leaves, boundaries['is_current_month']
            )
            _add_to_registry(registry, summary, 'monthly_summary')
        
        target_date_str = format_datetime(target_date, 'yyyy-MM-dd')
        
        # UPDATED: Fill in missing data using NEW working days calculation with leave types
        for emp_name, emp_data in registry.items():
            emp_holidays = employee_holidays.get(emp_name, [])
            emp_leaves = employee_leaves.get(emp_name, {})
            employee_info = emp_data['employee_info']

            if not emp_data['daily_data']:
                daily_summary = _calculate_daily_work_hours_with_status(
                    None,                       # no logs
                    employee_holidays,          # holiday mapping
                    employee_leaves,            # leave mapping
                    target_date_str,            # the current date string
                    employee_info               # employee info
                )
                if daily_summary:  # only add if function returned something valid
                    emp_data['daily_data'] = daily_summary


            if not emp_data['weekly_summary']:
                # Use NEW working days calculation with leave type considerations
                effective_working_days = _calculate_effective_working_days_new(
                    boundaries['week_start'], boundaries['summary_week_end'], emp_holidays,
                    emp_leaves, set(), boundaries['is_current_week']
                )

                holidays_in_week = [h for h in emp_holidays if boundaries['week_start'] <= h <= boundaries['week_end']]
                emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves.keys()]
                leaves_in_week = [l for l in emp_leave_dates if boundaries['week_start'] <= l <= boundaries['week_end']]
                
                emp_data['weekly_summary'] = {
                    "average_work_hours": 0.0, 
                    "total_hours_worked": 0.0, 
                    "total_days_worked": 0,
                    "effective_working_days": effective_working_days,
                    "employee_working_days": effective_working_days,
                    "holidays_in_period": len(holidays_in_week),
                    "leaves_in_period": len(leaves_in_week)
                }

            if not emp_data['monthly_summary']:
                # Use NEW working days calculation with leave type considerations
                effective_working_days = _calculate_effective_working_days_new(
                    boundaries['month_start'], boundaries['summary_month_end'], emp_holidays,
                    emp_leaves, set(), boundaries['is_current_month']
                )

                holidays_in_month = [h for h in emp_holidays if boundaries['month_start'] <= h <= boundaries['month_end']]
                emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves.keys()]
                leaves_in_month = [l for l in emp_leave_dates if boundaries['month_start'] <= l <= boundaries['month_end']]
                
                emp_data['monthly_summary'] = {
                    "average_work_hours": 0.0, 
                    "total_hours_worked": 0.0, 
                    "total_days_worked": 0,
                    "effective_working_days": effective_working_days,
                    "employee_working_days": effective_working_days,
                    "holidays_in_period": len(holidays_in_month),
                    "leaves_in_period": len(leaves_in_month)
                }

    except Exception as e:
        frappe.log_error("Error in processing data by periods", str(e))


# Structure final response with hierarchy (unchanged)
def _create_hierarchy_response(registry, manager_id, subordinate_ids):
    manager_data = registry.get(manager_id, {})
    subordinates_data = {emp_id: data for emp_id, data in registry.items() if emp_id in subordinate_ids}
    
    return {
        "user_id": manager_id,
        "manager_data": manager_data,
        "subordinates_data": subordinates_data,
        "total_count": len(subordinates_data) + (1 if manager_data else 0)
    }


# Main endpoint for fetching checkin data (unchanged)
@frappe.whitelist()
def fetch_checkins(from_date=None, to_date=None, specific_date=None):
    try:
        if from_date and not to_date: 
            frappe.throw("Please provide 'to_date' for date range.")
        if to_date and not from_date: 
            frappe.throw("Please provide 'from_date' for date range.")
        if (from_date or to_date) and specific_date: 
            frappe.throw("Provide either date range or specific date, not both.")
        
        if not any([from_date, to_date, specific_date]):
            specific_date = today()

        if from_date and to_date:
            try:
                start_date, end_date = getdate(from_date), getdate(to_date)
                processed_data, employee_holidays, employee_leaves= _get_processed_checkin_data(start_date, end_date)
                
                if not processed_data:
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                days_diff = (end_date - start_date).days + 1
                is_current_period = end_date >= getdate(today())
                    
                return _create_summary_with_effective_working_days(
                    processed_data, start_date, end_date, 
                    employee_holidays, employee_leaves, is_current_period
                )
                
            except Exception as e:
                frappe.log_error("Error in date range processing", str(e))
                return {"error": "Failed to process date range request"}

        elif specific_date:
            try:
                target_date = getdate(specific_date)
                if target_date > getdate(today()):
                    return {"error": "Cannot fetch data for future date."}

                if frappe.session.user == 'Administrator':
                    manager_id = "Administrator"
                    all_employees = frappe.get_all("Employee", 
                        filters=[['status', '=', 'Active']], 
                        fields=["name", 'department', 'reports_to', 'image','employee_name']
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
                        fields=["name", "department", "reports_to", 'image','employee_name']
                    )

                registry = _build_employee_registry(all_employees)
                boundaries = _get_date_boundaries(target_date)
                
                all_data, employee_holidays, employee_leaves = _get_processed_checkin_data(
                    boundaries['earliest_date'], boundaries['latest_date']
                )
                
                _process_data_by_periods(
                    registry, all_data, boundaries, target_date, 
                    employee_holidays, employee_leaves
                )

                return _create_hierarchy_response(registry, manager_id, subordinate_ids)
                
            except Exception as e:
                frappe.log_error("Error in specific date processing", str(e))
                return {"error": "Failed to process request"}

    except Exception as e:
        frappe.log_error("Error in main function", str(e))
        return {"error": "System error occurred."}