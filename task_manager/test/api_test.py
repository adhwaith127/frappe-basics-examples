import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar


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


# Get employee shift working days info
def _get_employee_shift_info():
    try:
        query = """
            SELECT em.name as employee, COALESCE(st.working_days, 6) as working_days
            FROM `tabEmployee` em
            LEFT JOIN `tabShift Type` st ON em.default_shift = st.name
            WHERE em.status = 'Active'
        """
        shift_data = frappe.db.sql(query, as_dict=True)
        
        employee_shift_info = {}
        for record in shift_data:
            employee_shift_info[record['employee']] = record['working_days']
            
        return employee_shift_info
        
    except Exception as e:
        frappe.log_error("Error in getting employee shift info", str(e))
        return {}


# Get employee checkin data with shift info
def _get_employee_data(start_date, end_date):
    try:
        query = """
                SELECT
                    ec.employee, ec.time, ec.log_type,
                    em.name, em.department, em.reports_to, em.default_shift, em.holiday_list, em.image,
                    st.end_time, st.working_days
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


# Check if a date is working day for specific employee based on their holiday list
def _is_working_day_for_employee(check_date, emp_holidays):
    weekday = check_date.weekday()  # 0=Monday, 6=Sunday
    
    # Sunday is always holiday (fixed)
    if weekday == 6:
        return False
    
    # Monday to Saturday: check if date is in employee's holiday list
    if check_date in emp_holidays:
        return False  # It's a holiday
    
    return True  # It's a working day


# Filter checkins to separate regular work from overtime work
def _filter_regular_work_checkins(raw_checkin_data, employee_holidays):
    try:
        regular_work = []
        overtime_work = []
        
        for record in raw_checkin_data:
            employee = record['employee']
            checkin_date = getdate(record['time'])
            emp_holidays = employee_holidays.get(employee, [])
            
            if _is_working_day_for_employee(checkin_date, emp_holidays):
                regular_work.append(record)
            else:
                overtime_work.append(record)
        
        return regular_work, overtime_work
        
    except Exception as e:
        frappe.log_error("Error in filtering regular work checkins", str(e))
        return [], []


# Calculate work hours (keeping existing logic)
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
                "checkin_pairs": [],
                "status": "Absent"
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

        # Handle unclosed IN session
        if last_in_time:
            if shift_end and last_in_time.date() < date.today():
                # Past day → auto-close with shift end
                session_duration = time_diff_in_hours(shift_end, last_in_time)
                total_working_hours += session_duration
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": shift_end.strftime("%H:%M"),
                    "duration": round(session_duration, 2),
                    "auto_closed": True
                })
                last_out_time = shift_end
            else:
                # Current day → keep session open
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": None,
                    "duration": 0.0,
                    "ongoing": True
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
            "checkin_pairs": checkin_pairs,
            "status": "Present"
        }

    except Exception as e:
        frappe.log_error("Error in calculating employee work hours", str(e)) 
        return {"error": "Error in calculating work hours"}


# Sort and process checkin data (modified to handle regular work only)
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
                        shift_end = (datetime.min + shift_end).time()
                    elif isinstance(shift_end, str):
                        shift_end = datetime.strptime(shift_end, "%H:%M").time()
                    elif isinstance(shift_end, datetime):
                        shift_end = shift_end.time() 
                    
                    if shift_end:
                        shift_end = datetime.combine(getdate(logs[0]['time']), shift_end)
                    daily_summary = _calculate_employee_work_hours(logs, shift_end)
                    daily_summaries.append(daily_summary)

        return daily_summaries

    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return []


# Enhanced daily work hours calculation with status and leave handling
def _calculate_daily_work_hours_with_status(logs, emp_holidays, emp_leaves, date_str, employee_info):
    try:
        if not logs:
            if not employee_info:
                return None
                
            if date_str in [str(d) for d in emp_holidays]:
                status = "Holiday"
            elif date_str in emp_leaves:
                status = "On Leave" 
            else:
                # Check if it's Sunday (always off day)
                check_date = getdate(date_str)
                if check_date.weekday() == 6:  # Sunday
                    status = "Off Day"
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

        # If logs exist, employee was present
        work_summary = _calculate_employee_work_hours(logs)
        
        return {
            "employee": work_summary['employee'],
            "department": work_summary['department'],
            "reports_to": work_summary['reports_to'], 
            "image": work_summary.get('image'),
            "date": work_summary['date'],
            "daily_working_hours": work_summary['daily_working_hours'],
            "entry_time": work_summary['entry_time'],
            "exit_time": work_summary['exit_time'],
            "status": "Present",
            "checkin_pairs": work_summary['checkin_pairs']
        }
        
    except Exception as e:
        frappe.log_error("Error in calculating daily work hours with status", str(e))
        return None


# Calculate dynamic working days based on actual attendance including holiday work
def _calculate_dynamic_working_days(start_date, end_date, employee_name, emp_holidays, emp_leaves, base_working_days, all_checkin_dates, is_current_period=False):
    try:
        # Determine actual date range
        today_date = getdate(today())
        yesterday = today_date - timedelta(days=1)
        
        if is_current_period:
            actual_end_date = min(end_date, yesterday)
        else:
            actual_end_date = end_date
            
        if actual_end_date < start_date:
            return 0
            
        # Convert emp_leaves to date objects
        emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
        
        # Count working days excluding holidays and leaves
        expected_working_days = 0
        holiday_work_days = 0
        
        current_date = start_date
        while current_date <= actual_end_date:
            weekday = current_date.weekday()
            
            # Skip Sundays (always off)
            if weekday == 6:
                current_date += timedelta(days=1)
                continue
                
            # Check if it's a regular working day
            is_working_day = _is_working_day_for_employee(current_date, emp_holidays)
            is_on_leave = current_date in emp_leave_dates
            
            if is_working_day and not is_on_leave:
                expected_working_days += 1
            elif not is_working_day and not is_on_leave:
                # Check if employee worked on this holiday
                if str(current_date) in all_checkin_dates:
                    holiday_work_days += 1
                    
            current_date += timedelta(days=1)
        
        # Dynamic working days = expected + holiday work
        dynamic_working_days = expected_working_days + holiday_work_days
        
        return max(dynamic_working_days, 0)
        
    except Exception as e:
        frappe.log_error("Error in calculating dynamic working days", str(e))
        return 0


# Process checkin data with new logic
def _get_processed_checkin_data(from_date, to_date):
    try:
        if not from_date or not to_date:
            return [], {}, {}, {}
        
        # Get all data
        raw_checkin_data = _get_employee_data(from_date, to_date)
        if not raw_checkin_data:
            return [], {}, {}, {}
        
        # Get holidays and leaves
        employee_holidays = _get_employee_holidays(from_date, to_date)
        employee_leaves = _get_leaves_for_period(from_date, to_date)
        employee_shift_info = _get_employee_shift_info()

        # Filter regular work from overtime work
        filtered_checkin_data, overtime_data = _filter_regular_work_checkins(raw_checkin_data, employee_holidays)

        # Process daily summaries for regular work only
        daily_summaries = _sort_checkin_data(filtered_checkin_data)

        return daily_summaries, employee_holidays, employee_leaves, employee_shift_info
        
    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return [], {}, {}, {}


# Create period summary with dynamic working days calculation
def _create_summary_with_dynamic_working_days(daily_records, period_type, start_date, end_date, employee_holidays, employee_leaves, employee_shift_info, is_current_period=False):
    try:
        # Group daily records by employee
        employee_stats = defaultdict(lambda: {
            'total_work_hours': 0.0, 
            'days_worked': 0, 
            'department': None, 
            'reports_to': None,
            'image': None,
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
        
        result = []
        for emp_name, stats in employee_stats.items():
            # Get employee specific data
            emp_holidays = employee_holidays.get(emp_name, [])
            emp_leaves = employee_leaves.get(emp_name, set())
            base_working_days = employee_shift_info.get(emp_name, 6)
            
            # Calculate dynamic working days
            dynamic_working_days = _calculate_dynamic_working_days(
                start_date, end_date, emp_name, emp_holidays, 
                emp_leaves, base_working_days, stats['checkin_dates'], is_current_period
            )
            
            # Calculate average hours
            avg_hours = round(stats['total_work_hours'] / dynamic_working_days, 2) if dynamic_working_days > 0 else 0
            
            # Convert emp_leaves to date objects for counting
            emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
            
            # Count holidays and leaves in period
            holidays_in_period = [h for h in emp_holidays if start_date <= h <= end_date]
            leaves_in_period = [l for l in emp_leave_dates if start_date <= l <= end_date]
            
            result.append({
                "employee": emp_name, 
                "average_work_hours": avg_hours, 
                "total_hours_worked": round(stats['total_work_hours'], 2),
                "total_days_worked": stats['days_worked'],
                "dynamic_working_days": dynamic_working_days,
                "holidays_in_period": len(holidays_in_period),
                "leaves_in_period": len(leaves_in_period)
            })
            
        return result
        
    except Exception as e:
        frappe.log_error("Error in creating summary with dynamic working days", str(e))
        return []


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


# Get all subordinates using breadth-first search (unchanged)
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


# Calculate week/month boundaries for given date (modified for current vs past logic)
def _get_date_boundaries(target_date):
    try:
        today_date = getdate(today())
        yesterday = today_date - timedelta(days=1)
        
        # Calculate month boundaries
        month_start = target_date.replace(day=1)
        _, days_in_month = calendar.monthrange(target_date.year, target_date.month)
        month_end = target_date.replace(day=days_in_month)
        
        # Calculate week boundaries (Monday to saturday)
        week_start = target_date - timedelta(days=target_date.weekday())
        week_end = week_start + timedelta(days=5)  # saturday
        
        # Check if target_date is in current period
        is_current_month = (target_date.year == today_date.year and target_date.month == today_date.month)
        is_current_week = (week_start <= today_date <= week_end)
        
        # Adjust boundaries for current periods
        if is_current_month and month_end >= today_date:
            month_end = yesterday
            
        if is_current_week and week_end >= today_date:
            week_end = yesterday
            
        return {
            "month_start": month_start,
            "month_end": month_end,
            "week_start": week_start,
            "week_end": week_end,
            "earliest_date": min(month_start, week_start),
            "latest_date": max(month_end, week_end),
            "is_current_month": is_current_month,
            "is_current_week": is_current_week
        }
        
    except Exception as e:
        frappe.log_error("Error in calculating date boundaries", str(e))
        return {}


# Create employee registry structure (unchanged)
def _build_employee_registry(employees):
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


# Add data to employee registry (unchanged)
def _add_to_registry(registry, data, data_key):
    for record in data:
        emp_name = record.get('employee')
        if emp_name and emp_name in registry:
            clean_record = record.copy()
            # Remove redundant fields but keep "status" for daily_data
            for field in ['employee', 'department', 'reports_to', 'image']:
                clean_record.pop(field, None)
            registry[emp_name][data_key] = clean_record



# Process and categorize data into daily/weekly/monthly periods (updated with new logic)
def _process_data_by_periods(registry, all_data, boundaries, target_date, employee_holidays, employee_leaves, employee_shift_info):
    try:
        if not all_data:
            all_data = []

        daily_data, weekly_data, monthly_data = [], [], []
        
        # Categorize data by periods
        for record in all_data:
            record_date = getdate(record['date'])
            if boundaries['month_start'] <= record_date <= boundaries['month_end']:
                monthly_data.append(record)
            if boundaries['week_start'] <= record_date <= boundaries['week_end']:
                weekly_data.append(record)
            if record_date == target_date:
                daily_data.append(record)

        # --- Daily data (ensure status always included) ---
        if daily_data:
            for record in daily_data:
                emp_name = record['employee']
                emp_holidays = employee_holidays.get(emp_name, [])
                emp_leaves = employee_leaves.get(emp_name, set())
                date_str = format_datetime(target_date, 'yyyy-MM-dd')
                daily_summary = _calculate_daily_work_hours_with_status(
                    [r for r in daily_data if r['employee'] == emp_name],
                    emp_holidays,
                    emp_leaves,
                    date_str,
                    registry[emp_name]['employee_info']
                )
                if daily_summary:
                    for field in ['employee', 'department', 'reports_to', 'image']:
                        daily_summary.pop(field, None)
                    registry[emp_name]['daily_data'] = daily_summary

        # --- Weekly summary ---
        if weekly_data:
            summary = _create_summary_with_dynamic_working_days(
                weekly_data, 'weekly', boundaries['week_start'], boundaries['week_end'], 
                employee_holidays, employee_leaves, employee_shift_info, boundaries['is_current_week']
            )
            _add_to_registry(registry, summary, 'weekly_summary')

        # --- Monthly summary ---
        if monthly_data:
            summary = _create_summary_with_dynamic_working_days(
                monthly_data, 'monthly', boundaries['month_start'], boundaries['month_end'], 
                employee_holidays, employee_leaves, employee_shift_info, boundaries['is_current_month']
            )
            _add_to_registry(registry, summary, 'monthly_summary')

        # --- Fill missing employees with default Absent/Holiday/Leave/OffDay ---
        target_date_str = format_datetime(target_date, 'yyyy-MM-dd')
        for emp_name, emp_data in registry.items():
            emp_holidays = employee_holidays.get(emp_name, [])
            emp_leaves = employee_leaves.get(emp_name, set())
            base_working_days = employee_shift_info.get(emp_name, 6)
            employee_info = emp_data['employee_info']

            # 1. Daily data
            if not emp_data['daily_data']:
                daily_summary = _calculate_daily_work_hours_with_status(
                    None, emp_holidays, emp_leaves, target_date_str, employee_info
                )
                if daily_summary:
                    for field in ['employee', 'department', 'reports_to', 'image']:
                        daily_summary.pop(field, None)
                    emp_data['daily_data'] = daily_summary

            # 2. Weekly summary (fallback)
            if not emp_data['weekly_summary']:
                dynamic_working_days = _calculate_dynamic_working_days(
                    boundaries['week_start'], boundaries['week_end'], emp_name,
                    emp_holidays, emp_leaves, base_working_days, 
                    set(), boundaries['is_current_week']
                )
                holidays_in_week = [h for h in emp_holidays if boundaries['week_start'] <= h <= boundaries['week_end']]
                emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
                leaves_in_week = [l for l in emp_leave_dates if boundaries['week_start'] <= l <= boundaries['week_end']]
                emp_data['weekly_summary'] = {
                    "average_work_hours": 0.0, 
                    "total_hours_worked": 0.0, 
                    "total_days_worked": 0,
                    "dynamic_working_days": dynamic_working_days,
                    "holidays_in_period": len(holidays_in_week),
                    "leaves_in_period": len(leaves_in_week)
                }

            # 3. Monthly summary (fallback)
            if not emp_data['monthly_summary']:
                dynamic_working_days = _calculate_dynamic_working_days(
                    boundaries['month_start'], boundaries['month_end'], emp_name,
                    emp_holidays, emp_leaves, base_working_days, 
                    set(), boundaries['is_current_month']
                )
                holidays_in_month = [h for h in emp_holidays if boundaries['month_start'] <= h <= boundaries['month_end']]
                emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
                leaves_in_month = [l for l in emp_leave_dates if boundaries['month_start'] <= l <= boundaries['month_end']]
                emp_data['monthly_summary'] = {
                    "average_work_hours": 0.0, 
                    "total_hours_worked": 0.0, 
                    "total_days_worked": 0,
                    "dynamic_working_days": dynamic_working_days,
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


# Main API endpoint (structure kept, logic updated)
@frappe.whitelist()
def fetch_checkins(from_date=None, to_date=None, specific_date=None):
    try:
        # Input validation (unchanged)
        if from_date and not to_date: 
            frappe.throw("Please provide 'to_date' for date range.")
        if to_date and not from_date: 
            frappe.throw("Please provide 'from_date' for date range.")
        if (from_date or to_date) and specific_date: 
            frappe.throw("Provide either date range or specific date, not both.")
        
        if not any([from_date, to_date, specific_date]):
            specific_date = today()

        # Handle date range request (updated to use new logic)
        if from_date and to_date:
            try:
                start_date, end_date = getdate(from_date), getdate(to_date)
                processed_data, employee_holidays, employee_leaves, employee_shift_info = _get_processed_checkin_data(start_date, end_date)
                
                if not processed_data:
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                days_diff = (end_date - start_date).days + 1
                period_type = 'weekly' if days_diff <= 7 else 'monthly'
                is_current_period = end_date >= getdate(today())
                    
                return _create_summary_with_dynamic_working_days(
                    processed_data, period_type, start_date, end_date, 
                    employee_holidays, employee_leaves, employee_shift_info, is_current_period
                )
                
            except Exception as e:
                frappe.log_error("Error in date range processing", str(e))
                return {"error": "Failed to process date range request"}

        # Handle specific date request (updated hierarchy and processing)
        elif specific_date:
            try:
                target_date = getdate(specific_date)
                if target_date > getdate(today()):
                    return {"error": "Cannot fetch data for future date."}
                
                # Determine user hierarchy (unchanged)
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

                # Build registry and process data (updated)
                registry = _build_employee_registry(all_employees)
                boundaries = _get_date_boundaries(target_date)

                all_data, employee_holidays, employee_leaves, employee_shift_info = _get_processed_checkin_data(
                    boundaries['earliest_date'], boundaries['latest_date']
                )
                _process_data_by_periods(
                    registry, all_data, boundaries, target_date, 
                    employee_holidays, employee_leaves, employee_shift_info
                )

                return _create_hierarchy_response(registry, manager_id, subordinate_ids)
                
            except Exception as e:
                frappe.log_error("Error in specific date processing", str(e))
                return {"error": "Failed to process request"}

    except Exception as e:
        frappe.log_error("Error in main function", str(e))
        return {"error": "System error occurred."}