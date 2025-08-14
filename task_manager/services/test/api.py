import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar
from typing import List, Dict, Any, Tuple, Optional, Set


def _get_working_days(start_date: date, end_date: date) -> int:
    try:
        if not all([isinstance(start_date, date), isinstance(end_date, date)]):
            raise TypeError("Both dates must be date objects")
        if start_date > end_date:
            raise ValueError("Start date cannot be greater than end date")

        day_diff = (end_date - start_date).days + 1
        full_weeks, extra_days = divmod(day_diff, 7)
        working_days = full_weeks * 5
        
        for i in range(extra_days):
            current_day_weekday = (start_date + timedelta(days=full_weeks * 7 + i)).weekday()
            if current_day_weekday < 5:
                working_days += 1
                
        return working_days
    
    except (TypeError, ValueError) as e:
        frappe.log_error(f"Working days error | Start: {start_date} | End: {end_date} | Error: {str(e)}", "Working_Days_Error")
        return 0
    except Exception as e:
        frappe.log_error(f"Unexpected working days error | Error: {str(e)}", "Working_Days_Unexpected")
        return 0


def _calculate_daily_work_hours(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    # Handle absent case with complete structure
    if not logs:
        return {
            "employee": None, "department": None, "reports_to": None,
            "date": None, "work_time": 0.0, "entry_time": None,
            "exit_time": None, "status": "Absent", "checkin_pairs": []
        }

    total_hours = 0.0
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
                work_duration = time_diff_in_hours(log['time'], last_in_time)
                total_hours += work_duration
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": log['time'].strftime("%H:%M"),
                    "duration": round(work_duration, 2)
                })
                last_in_time = None
            
            last_out_time = log['time']
    
    # Handle unmatched IN entries
    if last_in_time:
        checkin_pairs.append({
            "in_time": last_in_time.strftime("%H:%M"),
            "out_time": None,
            "duration": None
        })

    # Absent only if no logs at all
    if not first_in_time and not last_out_time:
        status = "Absent"
    else:
        status = "Present"

    return {
        "employee": logs[0]['employee'],
        "department": logs[0]['department'],
        "reports_to": logs[0]['reports_to'],
        "date": format_datetime(logs[0]['time'], 'yyyy-MM-dd'),
        "work_time": round(total_hours, 2),
        "entry_time": first_in_time.strftime("%H:%M") if first_in_time else None,
        "exit_time": last_out_time.strftime("%H:%M") if last_out_time else None,
        "status": status,
        "checkin_pairs": checkin_pairs
    }


def get_processed_checkin_data(from_date: date, to_date: date) -> List[Dict[str, Any]]:
    try:
        if not from_date or not to_date:
            return []
        
        date_condition = "DATE(ec.time) BETWEEN %(from_date)s AND %(to_date)s"
        query = f"""
            SELECT
                ec.employee, ec.time, ec.log_type,
                em.department, em.reports_to
            FROM `tabEmployee Checkin` AS ec
            JOIN `tabEmployee` AS em ON ec.employee = em.name
            WHERE {date_condition} AND em.status = 'Active'
            ORDER BY ec.employee, ec.time
        """
        params = {"from_date": from_date, "to_date": to_date}
        raw_data = frappe.db.sql(query, params, as_dict=True)
        if not raw_data: return []

        grouped_data = defaultdict(lambda: defaultdict(list))
        for entry in raw_data:
            date_str = format_datetime(entry['time'], 'yyyy-MM-dd')
            grouped_data[entry['employee']][date_str].append(entry)

        daily_summaries = []
        for employee, days in grouped_data.items():
            for date_key, logs in days.items():
                if logs:
                    daily_summary = _calculate_daily_work_hours(logs)
                    daily_summaries.append(daily_summary)
        
        return daily_summaries
    except Exception as e:
        frappe.log_error(f"Checkin data processing error | From: {from_date} | To: {to_date} | Error: {str(e)}", "Checkin_Processing_Error")
        return []


def _create_period_summary(daily_records: List[Dict[str, Any]], total_working_days: int, result_type: str) -> List[Dict[str, Any]]:
    summary_data = defaultdict(lambda: {'total_work_hours': 0.0, 'days_worked': 0, 'department': None, 'reports_to': None})

    for record in daily_records:
        emp = summary_data[record['employee']]
        emp['total_work_hours'] += record.get('work_time', 0.0)
        emp['days_worked'] += 1
        if not emp['department']: emp['department'] = record.get('department')
        if not emp['reports_to']: emp['reports_to'] = record.get('reports_to')
    
    result_list = []
    for emp_name, stats in summary_data.items():
        avg_hours = round(stats['total_work_hours'] / total_working_days, 2) if total_working_days > 0 else 0
        result_list.append({
            "employee": emp_name, "department": stats['department'], "reports_to": stats['reports_to'],
            "average_work_hours": avg_hours, "total_hours_worked": round(stats['total_work_hours'], 2),
            "total_days_worked": stats['days_worked'], "total_working_days_in_period": total_working_days,
            "result_type": result_type
        })
    return result_list


def _populate_registry(registry: Dict, data: List[Dict], data_key: str):
    for record in data:
        emp_name = record.get('employee')
        if emp_name and emp_name in registry:
            record_copy = record.copy()
            for field in ['employee', 'department', 'reports_to', 'result_type']:
                record_copy.pop(field, None)
            registry[emp_name][data_key] = record_copy


def get_hierarchy_map() -> defaultdict[str, List[str]]:
    try:
        employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "reports_to"])
        hierarchy = defaultdict(list)
        for emp in employees:
            if emp.reports_to:
                hierarchy[emp.reports_to].append(emp.name)
        return hierarchy
    except Exception as e:
        frappe.log_error(f"Hierarchy map error: {str(e)}", "Hierarchy_Error")
        return defaultdict(list)


def get_all_subordinates(manager_id: str, hierarchy_map: Dict) -> List[str]:
    try:
        if not manager_id or not hierarchy_map:
            return []
        
        # BFS approach
        all_subs = set()
        queue = hierarchy_map.get(manager_id, [])
        visited = set(queue)
        all_subs.update(queue)

        while queue:
            current_manager = queue.pop(0)
            direct_reports = hierarchy_map.get(current_manager, [])
            for report in direct_reports:
                if report not in visited:
                    visited.add(report)
                    all_subs.add(report)
                    queue.append(report)
        return list(all_subs)
    except Exception as e:
        frappe.log_error(f"Subordinates search error | Manager: {manager_id} | Error: {str(e)}", "Subordinates_Error")
        return []


def _get_date_boundaries(s_date: date) -> Dict[str, date]:
    today_date = getdate(today())
    
    month_start = s_date.replace(day=1)
    _, num_days_in_month = calendar.monthrange(s_date.year, s_date.month)
    month_end_of_calendar = s_date.replace(day=num_days_in_month)
    
    week_start = s_date - timedelta(days=s_date.weekday())
    week_end_of_calendar = week_start + timedelta(days=4)

    return {
        "month_start": month_start,
        "month_end": min(month_end_of_calendar, today_date),
        "week_start": week_start,
        "week_end": min(week_end_of_calendar, today_date),
        "earliest_fetch": min(month_start, week_start),
        "latest_fetch": max(min(month_end_of_calendar, today_date), min(week_end_of_calendar, today_date))
    }


def _process_and_populate_registry(registry: Dict, all_data: List[Dict], dates: Dict, s_date: date):
    if not all_data: return

    daily_data, weekly_data, monthly_data = [], [], []
    for record in all_data:
        record_date = getdate(record['date'])
        if dates['month_start'] <= record_date <= dates['month_end']:
            monthly_data.append(record)
        if dates['week_start'] <= record_date <= dates['week_end']:
            weekly_data.append(record)
        if record_date == s_date:
            daily_data.append(record)

    if daily_data:
        _populate_registry(registry, daily_data, 'daily_data')

    if weekly_data:
        working_days = _get_working_days(dates['week_start'], dates['week_end'])
        summary = _create_period_summary(weekly_data, working_days, "weekly_summary")
        _populate_registry(registry, summary, 'weekly_summary')

    if monthly_data:
        working_days = _get_working_days(dates['month_start'], dates['month_end'])
        summary = _create_period_summary(monthly_data, working_days, "monthly_summary")
        _populate_registry(registry, summary, 'monthly_summary')
    
    # Handle absent employees
    s_date_str = format_datetime(s_date, 'yyyy-MM-dd')
    for emp_name in registry:
        if not registry[emp_name]['daily_data']:
            registry[emp_name]['daily_data'] = {
                'date': s_date_str, 'work_time': 0.0, 'entry_time': None,
                'exit_time': None, 'status': 'Absent', 'checkin_pairs': []
            }


def _structure_data_for_hierarchy(registry: Dict, manager_id: str, subordinate_ids: List[str]) -> Dict[str, Any]:
    manager_data = registry.get(manager_id, {})
    subordinates_data = {emp_id: data for emp_id, data in registry.items() if emp_id in subordinate_ids}
    
    return {
        "user_id": manager_id,
        "manager_data": manager_data,
        "subordinates_data": subordinates_data,
        "total_count(with manager)": len(subordinates_data) + (1 if manager_data else 0)
    }


@frappe.whitelist()
def fetch_checkins(from_date: str = None, to_date: str = None, specific_date: str = None) -> Dict[str, Any]:
    try:
        # Input validation
        if from_date and not to_date: frappe.throw("Please provide a 'To Date' for the date range.")
        if to_date and not from_date: frappe.throw("Please provide a 'From Date' for the date range.")
        if (from_date or to_date) and specific_date: frappe.throw("Provide either a date range or a specific date, not both.")
        
        if not (from_date or to_date or specific_date):
            specific_date = today()
            
        # Handle Date Range Request
        if from_date and to_date:
            try:
                start_dt, end_dt = getdate(from_date), getdate(to_date)
                processed_data = get_processed_checkin_data(start_dt, end_dt)
                if not processed_data:
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                working_days = _get_working_days(start_dt, end_dt)
                return _create_period_summary(processed_data, working_days, "daterange_summary")
                
            except Exception as e:
                frappe.log_error(f"Date range processing error | From: {from_date} | To: {to_date} | Error: {str(e)}", "Daterange_Error")
                return {"error": "Failed to process date range request"}

        # Handle Specific Date Request
        elif specific_date:
            try:
                s_date = getdate(specific_date)
                if s_date > getdate(today()):
                    return {"error": "Cannot fetch data for a future date."}
                
                # User hierarchy determination
                if frappe.session.user == 'Administrator':
                    manager_id = "Administrator"
                    all_employees = frappe.get_all("Employee", filters=[['status', '=', 'Active']], fields=["name", 'department', 'reports_to'])
                    subordinate_ids = [emp.name for emp in all_employees if emp.name != manager_id]
                else:
                    manager_id = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
                    if not manager_id:
                        frappe.throw("Logged-in user is not linked to an active employee record.")
                    
                    hierarchy_map = get_hierarchy_map()
                    subordinate_ids = get_all_subordinates(manager_id, hierarchy_map)
                    allowed_employees_set = set(subordinate_ids + [manager_id])
                    all_employees = frappe.get_all("Employee", filters={"name": ["in", list(allowed_employees_set)]}, fields=["name", "department", "reports_to"])

                # Build registry
                employee_registry = {
                    emp.name: {
                        'employee_info': {'name': emp.name, 'department': emp.department, 'reports_to': emp.reports_to},
                        'daily_data': {}, 'weekly_summary': {}, 'monthly_summary': {}
                    } for emp in all_employees
                }
                
                # Process data
                date_boundaries = _get_date_boundaries(s_date)
                all_processed_data = get_processed_checkin_data(date_boundaries['earliest_fetch'], date_boundaries['latest_fetch'])
                _process_and_populate_registry(employee_registry, all_processed_data, date_boundaries, s_date)
                
                return _structure_data_for_hierarchy(employee_registry, manager_id, subordinate_ids)
                
            except Exception as e:
                frappe.log_error(f"Specific date error | User: {frappe.session.user} | Date: {specific_date} | Error: {str(e)}", "Specific_Date_Error")
                return {"error": "Failed to process request"}

    except Exception as e:
        frappe.log_error(f"Critical error | User: {frappe.session.user} | Params: {from_date}, {to_date}, {specific_date} | Error: {str(e)}", "Critical_Error")
        return {"error": "System error occurred."}