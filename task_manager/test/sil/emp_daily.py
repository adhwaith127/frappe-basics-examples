import frappe
from frappe.utils import today, getdate
from datetime import timedelta
import calendar
from frappe.utils import time_diff_in_hours, getdate, today
from collections import defaultdict
from datetime import datetime, date, timedelta
import calendar


def calculate_daily_work_hours(logs: list, shift_end_time: datetime = None) -> dict:
    if not logs:
        return {}

    total_hours = 0.0
    last_in_time = None
    first_in_time = None
    last_out_time = None
    checkin_pairs = []

    for log in sorted(logs, key=lambda x: x['time']):
        if log['log_type'] == "IN":
            if first_in_time is None:
                first_in_time = log['time']
            if last_in_time is None:
                last_in_time = log['time']

        elif log['log_type'] == "OUT":
            if last_in_time:
                duration = time_diff_in_hours(log['time'], last_in_time)
                total_hours += duration
                checkin_pairs.append({
                    "in_time": last_in_time.strftime("%H:%M"),
                    "out_time": log['time'].strftime("%H:%M"),
                    "duration": round(duration, 2)
                })
                last_in_time = None
            last_out_time = log['time']
    
    if last_in_time:
        checkin_pairs.append({
            "in_time": last_in_time.strftime("%H:%M"),
            "out_time": None,
            "duration": None
        })

    exit_time = None
    if last_out_time and not last_in_time:
        exit_time = last_out_time.strftime("%H:%M")

    return {
        "employee": logs[0].get('employee'),
        "department": logs[0].get('department'),
        "date": getdate(logs[0]['time']).isoformat(),
        "daily_working_hours": round(total_hours, 2),
        "entry_time": first_in_time.strftime("%H:%M") if first_in_time else None,
        "exit_time": exit_time,
        "checkin_pairs": checkin_pairs,
        "status": "Present"
    }

def process_daily_summaries(checkin_data: list) -> list:
    grouped_data = defaultdict(lambda: defaultdict(list))
    for entry in checkin_data:
        date_str = getdate(entry['time']).isoformat()
        grouped_data[entry['employee']][date_str].append(entry)

    daily_summaries = []
    for employee, days in grouped_data.items():
        for date_iso, logs in days.items():
            if not logs:
                continue

            shift_end = logs[0].get('end_time')
            shift_end_datetime = None
            log_date = getdate(logs[0]['time'])

            if isinstance(shift_end, timedelta):
                shift_end_datetime = datetime.combine(log_date, (datetime.min + shift_end).time())
            elif isinstance(shift_end, str):
                try:
                    shift_end_datetime = datetime.combine(log_date, datetime.strptime(shift_end, "%H:%M:%S").time())
                except ValueError:
                     shift_end_datetime = datetime.combine(log_date, datetime.strptime(shift_end, "%H:%M").time())
            elif isinstance(shift_end, datetime):
                 shift_end_datetime = shift_end

            summary = calculate_daily_work_hours(logs, shift_end_datetime)
            if summary:
                daily_summaries.append(summary)

    return daily_summaries

def calculate_effective_working_days(start_date: date, end_date: date, holidays: list, leaves: set, checkin_dates: set) -> int:
    effective_end_date = min(end_date, getdate(today()) - timedelta(days=1))
    if effective_end_date < start_date:
        return 0
    
    total_working_days = 0
    excluded_dates = set()
    
    for holiday in holidays:
        if start_date <= holiday <= effective_end_date:
            excluded_dates.add(holiday)
    
    for leave_date_str in leaves:
        leave_date = getdate(leave_date_str)
        if start_date <= leave_date <= effective_end_date:
            if leave_date not in checkin_dates:
                excluded_dates.add(leave_date)
    
    current_date = start_date
    while current_date <= effective_end_date:
        if current_date.weekday() < 5 and current_date not in excluded_dates:
            total_working_days += 1
        current_date += timedelta(days=1)
    
    return total_working_days

def calculate_period_average(daily_records: list, start_date: date, end_date: date, holidays: list = [], leaves: set = set()) -> dict:
    today_date = getdate(today())
    
    if today_date == start_date:
        average_hours = 0.0
        past_records = []
    else:
        past_records = [rec for rec in daily_records if getdate(rec['date']) < today_date]
        total_hours = sum(rec.get('daily_working_hours', 0.0) for rec in past_records)
        average_hours = round(total_hours / len(past_records), 2) if past_records else 0.0
    
    checkin_dates = {getdate(rec['date']) for rec in daily_records}
    effective_working_days = calculate_effective_working_days(start_date, end_date, holidays, leaves, checkin_dates)
    
    return {
        "total_hours_worked": round(sum(rec.get('daily_working_hours', 0.0) for rec in daily_records), 2),
        "average_work_hours": average_hours,
        "days_worked": len(daily_records),
        "total_working_days_in_period": effective_working_days
    }

def _get_employee_checkin_data_for_period(employee_name: str, start_date: str, end_date: str) -> list:
    try:
        query = """
            SELECT
                ec.employee, ec.time, ec.log_type,
                em.name, em.department, em.default_shift,
                st.end_time
            FROM `tabEmployee Checkin` AS ec
            JOIN `tabEmployee` AS em ON ec.employee = em.name
            LEFT JOIN `tabShift Type` AS st ON em.default_shift = st.name
            WHERE ec.employee = %(employee_name)s
              AND DATE(ec.time) BETWEEN %(start_date)s AND %(end_date)s
            ORDER BY ec.time
        """
        return frappe.db.sql(query, {
            "employee_name": employee_name,
            "start_date": start_date,
            "end_date": end_date
        }, as_dict=True)
    except Exception as e:
        frappe.log_error("Error fetching employee check-in data", str(e))
        return []

def _get_employee_leaves_for_period(employee_name: str, start_date: str, end_date: str) -> set:
    try:
        leaves_data = frappe.get_all(
            "Leave Application",
            filters={
                "employee": employee_name,
                "status": "Approved",
                "docstatus": 1,
                "from_date": ["<=", end_date],
                "to_date": [">=", start_date],
            },
            fields=["from_date", "to_date"]
        )
        leave_dates = set()
        start = getdate(start_date)
        end = getdate(end_date)
        for leave in leaves_data:
            current_date = max(getdate(leave.from_date), start)
            while current_date <= min(getdate(leave.to_date), end):
                leave_dates.add(current_date.isoformat())
                current_date += timedelta(days=1)
        return leave_dates
    except Exception as e:
        frappe.log_error("Error fetching employee leaves", str(e))
        return set()
        
def _get_employee_holidays_for_period(employee_name: str, start_date: str, end_date: str) -> list:
    try:
        holiday_list = frappe.db.get_value("Employee", employee_name, "holiday_list")
        if not holiday_list:
            return []
        
        holidays = frappe.get_all(
            "Holiday",
            filters={
                "parent": holiday_list,
                "holiday_date": ["between", [start_date, end_date]]
            },
            pluck="holiday_date"
        )
        return holidays or []
    except Exception as e:
        frappe.log_error("Error fetching employee holidays", str(e))
        return []

@frappe.whitelist(allow_guest=True)
def get_employee_details(user_id: str, select_date: str = None):
    if not frappe.db.exists("User", user_id):
        return {"success": False, "message": f"User {user_id} not found"}

    employee = frappe.db.get_value(
        "Employee",
        {"user_id": user_id, "status": "Active"},
        ["name", "employee_name", "department", "designation"],
        as_dict=True
    )

    if not employee:
        return {"success": False, "message": f"No active Employee linked with user {user_id}"}

    target_date = getdate(select_date) if select_date else getdate(today())
    emp_name = employee['name']

    month_start = target_date.replace(day=1)
    month_end = target_date.replace(day=calendar.monthrange(target_date.year, target_date.month)[1])
    week_start = target_date - timedelta(days=target_date.weekday())
    week_end = week_start + timedelta(days=6)

    earliest_date = min(month_start, week_start)

    checkin_data = _get_employee_checkin_data_for_period(emp_name, earliest_date.isoformat(), target_date.isoformat())
    leaves = _get_employee_leaves_for_period(emp_name, earliest_date.isoformat(), month_end.isoformat())
    holidays = _get_employee_holidays_for_period(emp_name, earliest_date.isoformat(), month_end.isoformat())

    daily_summaries = process_daily_summaries(checkin_data)

    target_date_str = target_date.isoformat()
    daily_data = next((s for s in daily_summaries if s['date'] == target_date_str), None)
    
    if not daily_data:
        status = "Absent"
        if target_date in holidays:
            status = "Holiday"
        elif target_date_str in leaves:
            status = "On Leave"
        
        daily_data = {
            "date": target_date_str,
            "daily_working_hours": 0.0,
            "entry_time": None,
            "exit_time": None,
            "checkin_pairs": [],
            "status": status
        }

    weekly_records = [s for s in daily_summaries if week_start <= getdate(s['date']) <= week_end]
    monthly_records = [s for s in daily_summaries if month_start <= getdate(s['date']) <= month_end]

    weekly_summary = calculate_period_average(weekly_records, week_start, week_end, holidays, leaves)
    monthly_summary = calculate_period_average(monthly_records, month_start, month_end, holidays, leaves)

    return {
        "success": True,
        "employee_details": {
            "name": employee['employee_name'],
            "department": employee['department'],
            "designation": employee['designation']
        },
        "selected_date_data": daily_data,
        "weekly_summary": weekly_summary,
        "monthly_summary": monthly_summary
    }