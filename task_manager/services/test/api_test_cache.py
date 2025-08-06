import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar

# --- Helper functions remain the same ---

# Calculates the number of working days (Mon-Fri) in a given date range.
def _get_working_days(start_date, end_date):
    try:
        if not all([isinstance(start_date, date), isinstance(end_date, date)]):
            raise TypeError("Both dates must be date objects")
        if start_date > end_date:
            raise ValueError("Start date cannot be greater than end date")
        
        working_days = 0
        current_date = start_date
        while current_date <= end_date:
            if current_date.weekday() < 5:
                working_days += 1
            current_date += timedelta(days=1)
        return working_days
    
    except (TypeError, ValueError) as e:
        frappe.log_error(f"Working days error | Start: {start_date} | End: {end_date} | Error: {str(e)}", "Working_Days_Error")
        return 0
    except Exception as e:
        frappe.log_error(f"Unexpected working days error | Error: {str(e)}", "Working_Days_Unexpected")
        return 0


# calculates total work hours from a sorted list of daily check-in logs.
def _calculate_daily_work_hours(logs):
    total_hours = 0.
    last_in_time = None

    for log in logs:
        if log['log_type'] == "IN" and last_in_time is None:
            last_in_time = log['time']
        elif log['log_type'] == "OUT" and last_in_time is not None:
            total_hours += time_diff_in_hours(log['time'], last_in_time)
            last_in_time = None

    first_log_time = logs[0]['time'].strftime("%H:%M")
    last_log_time = logs[-1]['time'].strftime("%H:%M")

    return {
        "employee": logs[0]['employee'],
        "department": logs[0]['department'],
        "reports_to": logs[0]['reports_to'],
        "date": format_datetime(logs[0]['time'], 'yyyy-MM-dd'),
        "work_time": round(total_hours, 2),
        "entry_time": first_log_time,
        "exit_time": last_log_time
    }

# --- Caching added to data fetching functions ---

# Fetches, groups, and processes check-in data for a given date range.
def get_processed_checkin_data(from_date, to_date):
    # --- CACHE LOGIC ---
    # Only cache results for date ranges that are entirely in the past.
    # Data for today is volatile and should not be cached here.
    is_past_data = getdate(to_date) < getdate(today())
    cache_key = f"processed_checkin_data:{from_date}:{to_date}"
    
    if is_past_data:
        cached_data = frappe.cache().get(cache_key)
        if cached_data:
            return cached_data
    # --- END CACHE LOGIC ---

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
        if not raw_data:
            return []

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
        
        # --- CACHE LOGIC ---
        # Cache the result for 24 hours if it's past data.
        if is_past_data:
            frappe.cache().set(cache_key, daily_summaries, expires_in_sec=86400) # 24 hours
        # --- END CACHE LOGIC ---

        return daily_summaries

    except frappe.SQLError as e:
        frappe.log_error(f"Database error in checkin data | From: {from_date} | To: {to_date} | Error: {str(e)}", "Checkin_DB_Error")
        return []
    except Exception as e:
        frappe.log_error(f"Checkin data processing error | From: {from_date} | To: {to_date} | Error: {str(e)}", "Checkin_Processing_Error")
        return []


# function to create a summary from a list of daily processed records.
def _create_period_summary(daily_records, total_working_days, result_type):
    summary_data = defaultdict(lambda: {
        'total_work_hours': 0.0, 'days_worked': 0,
        'department': None, 'reports_to': None
    })

    for record in daily_records:
        emp = summary_data[record['employee']]
        emp['total_work_hours'] += record['work_time']
        emp['days_worked'] += 1
        emp['department'] = record['department']
        emp['reports_to'] = record['reports_to']
    
    result_list = []
    for emp_name, stats in summary_data.items():
        avg_hours = round(stats['total_work_hours'] / total_working_days, 2) if total_working_days > 0 else 0
        
        result_list.append({
            "employee": emp_name, 
            "department": stats['department'],
            "reports_to": stats['reports_to'], 
            "average_work_hours": avg_hours,
            "total_hours_worked": round(stats['total_work_hours'], 2),
            "total_days_worked": stats['days_worked'],
            "total_working_days_in_period": total_working_days,
            "result_type": result_type
        })
    return result_list

# Populates a pre-existing registry with attendance data.
def _populate_registry(registry, data, data_key):
    for record in data:
        emp_name = record['employee']
        if emp_name in registry:
            record_copy = record.copy()
            record_copy.pop('employee', None)
            record_copy.pop('department', None)
            record_copy.pop('reports_to', None)
            record_copy.pop('result_type', None)
            registry[emp_name][data_key] = record_copy
    return registry


# Builds a map of manager to their direct reports(a hierarchy list) 
def get_hierarchy_map():
    # --- CACHE LOGIC ---
    # Hierarchy changes infrequently. Cache for 6 hours.
    cache_key = "employee_hierarchy_map"
    cached_map = frappe.cache().get(cache_key)
    if cached_map:
        return cached_map
    # --- END CACHE LOGIC ---
    try:
        employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "reports_to"])
        if not employees:
            return defaultdict(list)
        
        hierarchy = defaultdict(list)
        for emp in employees:
            if emp.reports_to:
                hierarchy[emp.reports_to].append(emp.name)

        # --- CACHE LOGIC ---
        frappe.cache().set(cache_key, hierarchy, expires_in_sec=21600) # 6 hours
        # --- END CACHE LOGIC ---

        return hierarchy

    except frappe.SQLError as e:
        frappe.log_error(f"Database error in hierarchy map | Error: {str(e)}", "Hierarchy_DB_Error")
        return defaultdict(list)
    except Exception as e:
        frappe.log_error(f"Hierarchy map error | Error: {str(e)}", "Hierarchy_Error")
        return defaultdict(list)

# finds all direct and indirect subordinates for a given manager
def get_all_subordinates(manager_id, hierarchy_map):
    # This function is computationally cheap and depends on `hierarchy_map` which is already cached.
    # No need to cache this function itself.
    try:
        if not manager_id or not hierarchy_map:
            return []
        
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

# Structures the pre-filtered employee registry into a final format.
def _structure_data_for_hierarchy(employee_registry, manager_id, subordinate_ids):
    manager_data = employee_registry.get(manager_id, {})
    subordinates_data = {
        emp_id: data
        for emp_id, data in employee_registry.items() 
        if emp_id in subordinate_ids
    }
    return {
        "manager_data": manager_data,
        "subordinates_data": subordinates_data,
        "total_count(with manager)": len(subordinates_data) + (1 if manager_data else 0)
    }

# --- Main API Function with Top-Level Caching ---

@frappe.whitelist()
def fetch_checkins(from_date=None, to_date=None, specific_date=None):
    try:
        # --- 1. Input Validation ---
        if from_date and not to_date: frappe.throw("Please provide a 'To Date' for the date range.")
        if to_date and not from_date: frappe.throw("Please provide a 'From Date' for the date range.")
        if (from_date or to_date) and specific_date: frappe.throw("Provide either a date range or a specific date, not both.")
        
        if not (from_date or to_date or specific_date):
            specific_date = today()
            
        # --- 2. Handle Date Range Request ---
        if from_date and to_date:
            # --- CACHE LOGIC ---
            # We cache the final response. Only cache if the range is in the past.
            start, end = getdate(from_date), getdate(to_date)
            is_past_range = end < getdate(today())
            cache_key = f"fetch_checkins_range:{from_date}:{to_date}"

            if is_past_range:
                cached_response = frappe.cache().get(cache_key)
                if cached_response:
                    return cached_response
            # --- END CACHE LOGIC ---

            try:
                processed_data = get_processed_checkin_data(start, end)
                if not processed_data:
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                working_days = _get_working_days(start, end)
                response = _create_period_summary(processed_data, working_days, "daterange_summary")

                # --- CACHE LOGIC ---
                if is_past_range:
                    frappe.cache().set(cache_key, response, expires_in_sec=86400) # 24 hours
                # --- END CACHE LOGIC ---
                return response

            except Exception as e:
                frappe.log_error(f"Date range processing error | From: {from_date} | To: {to_date} | Error: {str(e)}", "Daterange_Error")
                return {"error": "Failed to process date range request"}

        # --- 3. Handle Specific Date Request ---
        elif specific_date:
            s_date = getdate(specific_date)
            # Hardcoded user_id for demonstration. In production, this should be dynamic.
            # user_id = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
            user_id="MURALY G"

            # --- CACHE LOGIC ---
            # Cache the final response. Use a short TTL for today, long TTL for past days.
            is_today = s_date == getdate(today())
            cache_key = f"fetch_checkins_specific_date:{user_id}:{specific_date}"
            ttl = 600 if is_today else 86400 # 10 minutes for today, 24 hours for past

            cached_response = frappe.cache().get(cache_key)
            if cached_response:
                return cached_response
            # --- END CACHE LOGIC ---
                
            try:
                if s_date > getdate(today()):
                    return {"error": "Cannot fetch data for a future date."}
                
                # if frappe.session.user != "Administrator" and not user_id:
                #     frappe.throw("Logged-in user is not linked to an active employee record.")
                user_id="MURALY G"

                hierarchy = get_hierarchy_map()
                subordinates = get_all_subordinates(user_id, hierarchy)
                allowed_employees = set(subordinates + [user_id])

                employee_details = frappe.get_all("Employee",
                    filters={"name": ["in", list(allowed_employees)]},
                    fields=["name", "department", "reports_to"]
                )
                
                employee_registry = {
                    emp.name: {
                        'employee_info': {'name': emp.name, 'department': emp.department, 'reports_to': emp.reports_to},
                        'daily_data': {}, 'weekly_summary': {}, 'monthly_summary': {}
                    } for emp in employee_details
                }
                
                month_start = s_date.replace(day=1)
                week_start = s_date - timedelta(days=s_date.weekday())
                week_end = min(s_date, week_start + timedelta(days=4))
                
                all_processed_data = get_processed_checkin_data(month_start, s_date)
                
                if all_processed_data:
                    monthly_working_days = _get_working_days(month_start, s_date)
                    monthly_summary = _create_period_summary(all_processed_data, monthly_working_days, "monthly_summary")
                    _populate_registry(employee_registry, monthly_summary, 'monthly_summary')
                    
                    weekly_data_list = [d for d in all_processed_data if week_start <= getdate(d['date']) <= week_end]
                    if weekly_data_list:
                        weekly_working_days = _get_working_days(week_start, week_end)
                        weekly_summary = _create_period_summary(weekly_data_list, weekly_working_days, "weekly_summary")
                        _populate_registry(employee_registry, weekly_summary, 'weekly_summary')
                    
                    daily_data = [d for d in all_processed_data if getdate(d['date']) == s_date]
                    if daily_data:
                        _populate_registry(employee_registry, daily_data, 'daily_data')
                
                for emp_name in employee_registry.keys():
                    if not employee_registry[emp_name]['daily_data']:
                        employee_registry[emp_name]['daily_data'] = {
                            'date': format_datetime(s_date, 'yyyy-MM-dd'),
                            'work_time': 0.0, 'entry_time': None, 'exit_time': None, 'status': 'absent'
                        }
                
                response = _structure_data_for_hierarchy(employee_registry, user_id, subordinates)

                # --- CACHE LOGIC ---
                frappe.cache().set(cache_key, response, expires_in_sec=ttl)
                # --- END CACHE LOGIC ---

                return response

            except frappe.SQLError as e:
                frappe.log_error(f"Database error | User: {frappe.session.user} | Date: {specific_date} | Error: {str(e)}", "Specific_Date_DB_Error")
                return {"error": "Database operation failed"}
            except Exception as e:
                frappe.log_error(f"Specific date error | User: {frappe.session.user} | Date: {specific_date} | Error: {str(e)}", "Specific_Date_Error")
                return {"error": "Failed to process request"}

    except Exception as e:
        frappe.log_error(f"Critical error | User: {frappe.session.user} | Params: {from_date}, {to_date}, {specific_date} | Error: {str(e)}", "Critical_Error")
        return {"error": "System error occurred."}