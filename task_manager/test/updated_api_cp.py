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
            SELECT em.name as employee, COALESCE(st.custom_working_days, 6) as working_days
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
                    em.name,em.employee_name as emp_display_name, em.department, em.reports_to, em.default_shift, em.holiday_list, em.image,
                    st.end_time, st.custom_working_days as working_days
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
            candidate_close_time = shift_end if shift_end else default_close_time
            effective_close_time = max(last_in_time, candidate_close_time)

            should_auto_close = (
                (log_date < current_date) or
                (log_date == current_date and current_time >= effective_close_time)
            )

            if should_auto_close:
                session_duration = time_diff_in_hours(effective_close_time, last_in_time)
                total_working_hours += session_duration
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": effective_close_time.strftime("%H:%M"),
                    "duration": round(session_duration, 2),
                    "auto_closed": True
                })
                last_out_time = effective_close_time
            else:
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": "Ongoing",
                    "duration": 0.0,
                    "ongoing": True
                })
                last_out_time = None                # set here to set last out time
                                                    # as none if checking pair is incomplete
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
            "has_ongoing_session": last_in_time is not None and not (
                (log_date < current_date) or 
                (log_date == current_date and current_time >= effective_close_time)
            )
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


# daily work hours calculation with status and leave
def _calculate_daily_work_hours_with_status(logs, employee_holidays, employee_leaves, date_str, employee_info):
    
    if not logs:
        if not employee_info:
            return None
            
        check_date = getdate(date_str)
        
        if date_str in employee_leaves:
            status = "On Leave"
        elif check_date in employee_holidays:
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


# Calculate effective working days based on 7-day period minus holidays and leaves
def _calculate_effective_working_days(start_date, end_date, emp_holidays, emp_leaves, emp_working_days, checkin_dates, is_current_period=False):
    try:
        today_date = getdate(today())
        
        if is_current_period:
            actual_end_date = min(end_date, today_date - timedelta(days=1))
        else:
            actual_end_date = end_date
            
        if actual_end_date < start_date:
            return 0
            
        emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
        
        total_days_in_period = (actual_end_date - start_date).days + 1
        
        holidays_in_period = [h for h in emp_holidays if start_date <= h <= actual_end_date]
        leaves_in_period = [l for l in emp_leave_dates if start_date <= l <= actual_end_date]
        
        non_working_days = set(holidays_in_period + leaves_in_period)
        
        effective_working_days = total_days_in_period - len(non_working_days)
        
        return max(effective_working_days, 0)
        
    except Exception as e:
        frappe.log_error("Error in calculating effective working days", str(e))
        return 0


# Process checkin data with holiday and leave
def _get_processed_checkin_data(from_date, to_date):
    try:
        if not from_date or not to_date:
            return [], {}, {}, {}
        # debug line for fetching date range
        frappe.log_error("DEBUG: Fetching data from", f"from_date: {from_date}, to_date: {to_date}")
        
        raw_checkin_data = _get_employee_data(from_date, to_date)
        if not raw_checkin_data:
            return [], {}, {}, {} 
        
        employee_holidays = _get_employee_holidays(from_date, to_date)
        employee_leaves = _get_leaves_for_period(from_date, to_date)
        employee_shift_info = _get_employee_shift_info()

        if not employee_holidays:
            frappe.log_error("No holiday data found", "Holiday mapping is empty")

        filtered_checkin_data = _filter_checkins(raw_checkin_data, employee_holidays)

        daily_summaries = _sort_checkin_data(filtered_checkin_data)
        if not daily_summaries:
            return [], employee_holidays, employee_leaves, employee_shift_info

        enhanced_summaries = []
        for summary in daily_summaries:
            emp_holidays = employee_holidays.get(summary['employee'], [])
            emp_leaves = employee_leaves.get(summary['employee'], set())
            
            # employee_info = {
            #     'name': summary['employee'],
            #     'department': summary['department'],
            #     'reports_to': summary['reports_to'],
            #     'image': summary.get('image')
            # }
            employee_info=_build_employee_info(summary)
            
            enhanced = _calculate_daily_work_hours_with_status(
                None,
                emp_holidays,
                emp_leaves,
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

        return enhanced_summaries, employee_holidays, employee_leaves, employee_shift_info
        
    except Exception as e:
        frappe.log_error("Error in processing checkin data", str(e))
        return [], {}, {}, {}  


# Create period summary with effective working days
def _create_summary_with_effective_working_days(daily_records, start_date, end_date, employee_holidays, employee_leaves, employee_shift_info, is_current_period=False):
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
            if not emp['emp_display_name']: emp['emp_display_name'] = record.get('emp_display_name')  # ADD THIS LINE
        
        result = []
        for emp_name, stats in employee_stats.items():
            emp_holidays = employee_holidays.get(emp_name, [])
            emp_leaves = employee_leaves.get(emp_name, set())
            emp_working_days = employee_shift_info.get(emp_name, 6)
            
            effective_working_days = _calculate_effective_working_days(
                start_date, end_date, emp_holidays, emp_leaves, 
                emp_working_days, stats['checkin_dates'], is_current_period
            )
            
            avg_hours = round(stats['total_work_hours'] / effective_working_days, 2) if effective_working_days > 0 else 0
            
            emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
            
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


# Add data to employee registry
def _add_to_registry(registry, data, data_key):
    for record in data:
        emp_name = record.get('employee')
        if emp_name and emp_name in registry:
            clean_record = record.copy()
            for field in ['employee', 'department', 'reports_to', 'image']:
                clean_record.pop(field, None)
            registry[emp_name][data_key] = clean_record

# manager to subordinates mapping
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


# Get all subordinates using bfs
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


# Calculate week/month boundaries with current vs past 
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
            "month_end": fetch_month_end,           # For data fetching - includes today
            "week_start": week_start,
            "week_end": fetch_week_end,             # For data fetching - includes today
            "summary_month_end": summary_month_end, # For summary calcs - excludes today
            "summary_week_end": summary_week_end,   # For summary calcs - excludes today
            "earliest_date": min(month_start, week_start),
            "latest_date": max(fetch_month_end, fetch_week_end),
            "is_current_month": is_current_month,
            "is_current_week": is_current_week
        }
        
    except Exception as e:
        frappe.log_error("Error in calculating date boundaries", str(e))
        return {}


# Create employee registry structure
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


# Process and categorize data into daily/weekly/monthly periods
def _process_data_by_periods(registry, all_data, boundaries, target_date, employee_holidays, employee_leaves, employee_shift_info):
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

        # Process weekly summary (excludes today)
        if weekly_data:
            summary = _create_summary_with_effective_working_days(
                weekly_data,  boundaries['week_start'], boundaries['summary_week_end'], 
                employee_holidays, employee_leaves, employee_shift_info, boundaries['is_current_week']
            )
            _add_to_registry(registry, summary, 'weekly_summary')

        # Process monthly summary (excludes today)
        if monthly_data:
            summary = _create_summary_with_effective_working_days(
                monthly_data, boundaries['month_start'], boundaries['summary_month_end'], 
                employee_holidays, employee_leaves, employee_shift_info, boundaries['is_current_month']
            )
            _add_to_registry(registry, summary, 'monthly_summary')
        
        target_date_str = format_datetime(target_date, 'yyyy-MM-dd')
        
        for emp_name, emp_data in registry.items():
            emp_holidays = employee_holidays.get(emp_name, [])
            emp_leaves = employee_leaves.get(emp_name, set())
            emp_working_days = employee_shift_info.get(emp_name, 6)
            employee_info = emp_data['employee_info']

            if not emp_data['daily_data']:
                daily_summary = _calculate_daily_work_hours_with_status(
                    None, emp_holidays, emp_leaves, target_date_str, employee_info
                )
                if daily_summary:
                    for field in ['employee', 'department', 'reports_to', 'image']:
                        daily_summary.pop(field, None)
                    emp_data['daily_data'] = daily_summary

            if not emp_data['weekly_summary']:
                effective_working_days = _calculate_effective_working_days(
                    boundaries['week_start'], boundaries['summary_week_end'], emp_holidays,  # Use summary_week_end
                    emp_leaves, emp_working_days, set(), boundaries['is_current_week']
                )

                holidays_in_week = [h for h in emp_holidays if boundaries['week_start'] <= h <= boundaries['week_end']]
                emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
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
                effective_working_days = _calculate_effective_working_days(
                    boundaries['month_start'], boundaries['summary_month_end'], emp_holidays,  # Use summary_month_end
                    emp_leaves, emp_working_days, set(), boundaries['is_current_month']
                )

                holidays_in_month = [h for h in emp_holidays if boundaries['month_start'] <= h <= boundaries['month_end']]
                emp_leave_dates = [getdate(leave_str) for leave_str in emp_leaves]
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


# Structure final response with hierarchy
def _create_hierarchy_response(registry, manager_id, subordinate_ids):
    manager_data = registry.get(manager_id, {})
    subordinates_data = {emp_id: data for emp_id, data in registry.items() if emp_id in subordinate_ids}
    
    return {
        "user_id": manager_id,
        "manager_data": manager_data,
        "subordinates_data": subordinates_data,
        "total_count": len(subordinates_data) + (1 if manager_data else 0)
    }


# Main endpoint for fetching checkin data 
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
                processed_data, employee_holidays, employee_leaves, employee_shift_info = _get_processed_checkin_data(start_date, end_date)
                
                if not processed_data:
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                days_diff = (end_date - start_date).days + 1
                period_type = 'weekly' if days_diff <= 7 else 'monthly'
                is_current_period = end_date >= getdate(today())
                    
                return _create_summary_with_effective_working_days(
                    processed_data, period_type, start_date, end_date, 
                    employee_holidays, employee_leaves, employee_shift_info, is_current_period
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
                
                # debug line to find all the returned date boundaries
                frappe.log_error("DEBUG: Date boundaries", boundaries)

                all_data, employee_holidays, employee_leaves, employee_shift_info = _get_processed_checkin_data(
                    boundaries['earliest_date'], boundaries['latest_date']
                )
                
                # doesnt return anything but we actually edit the regoistry inside this function
                # that is why we dont return anything or assign anything in the below line
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