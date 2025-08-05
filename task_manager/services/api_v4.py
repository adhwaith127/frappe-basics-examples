import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today, getdate
from collections import OrderedDict, defaultdict
from datetime import datetime, date, timedelta
import calendar


# ============================================================================
# SECTION 1: CORE DATA FETCHING - Single optimized query for all data
# ============================================================================

def get_comprehensive_data(from_date=None, to_date=None, specific_date=None, user_emp_id=None):
    """
    Single function to fetch all required data in one database call
    This reduces multiple queries and improves performance
    """
    
    # Set date conditions
    if from_date and to_date:
        date_condition = "DATE(ec.time) BETWEEN %s AND %s"
        params = [from_date, to_date]
    elif specific_date:
        date_condition = "DATE(ec.time) = %s"
        params = [specific_date]
    else:
        frappe.throw("Invalid date parameters")

    # Single comprehensive query to get:
    # 1. All employee hierarchy data
    # 2. All checkin data for the date range
    # 3. Employee details
    comprehensive_query = """
        SELECT 
            -- Employee basic info
            emp.name as employee_id,
            emp.employee_name,
            emp.department,
            emp.reports_to,
            emp.status,
            
            -- Checkin data (will be NULL if no checkin for this employee)
            ec.time,
            ec.log_type,
            
            -- Flag to identify if employee has any checkin data
            CASE WHEN ec.employee IS NOT NULL THEN 1 ELSE 0 END as has_checkin_data
        FROM 
            `tabEmployee` emp
        LEFT JOIN 
            `tabEmployee Checkin` ec ON emp.name = ec.employee 
            AND {date_condition}
        WHERE 
            emp.status = 'Active'
        ORDER BY 
            emp.name ASC, ec.time ASC
    """.format(date_condition=date_condition)
    
    try:
        comprehensive_data = frappe.db.sql(comprehensive_query, params, as_dict=True)
        return comprehensive_data
        
    except Exception as e:
        frappe.log_error(f"Comprehensive data fetch error: {str(e)}", "Data Fetch Failure")
        return {"error": "Failed to fetch comprehensive data"}


# ============================================================================
# SECTION 2: HIERARCHY PROCESSING - Build complete organizational structure
# ============================================================================

def build_complete_hierarchy(comprehensive_data, user_emp_id):
    """
    Build complete hierarchy map and identify user's subordinates
    Returns both direct and indirect subordinates
    """
    
    # Build manager -> subordinates mapping
    hierarchy_map = defaultdict(list)
    all_employees = {}
    
    # Process each employee record (deduplicate by employee_id)
    processed_employees = {}
    for record in comprehensive_data:
        emp_id = record['employee_id']
        if emp_id not in processed_employees:
            processed_employees[emp_id] = {
                'employee_id': emp_id,
                'employee_name': record['employee_name'],
                'department': record['department'],
                'reports_to': record['reports_to'],
                'status': record['status']
            }
            
            all_employees[emp_id] = processed_employees[emp_id]
            
            # Build hierarchy
            if record['reports_to']:
                hierarchy_map[record['reports_to']].append(emp_id)
    
    # Find all subordinates (direct and indirect) for the user
    def get_all_subordinates(manager_id, visited=None):
        """Recursively get all subordinates under a manager"""
        if visited is None:
            visited = set()
        if manager_id in visited:
            return []
        visited.add(manager_id)
        
        all_subs = []
        direct_subs = hierarchy_map.get(manager_id, [])
        
        for sub in direct_subs:
            all_subs.append(sub)
            # Get indirect subordinates
            all_subs.extend(get_all_subordinates(sub, visited.copy()))
        
        return all_subs
    
    # Get user's complete subordinate list
    user_subordinates = get_all_subordinates(user_emp_id)
    
    return {
        'hierarchy_map': hierarchy_map,
        'all_employees': all_employees,
        'user_subordinates': user_subordinates,
        'total_hierarchy_count': len(user_subordinates)
    }


# ============================================================================
# SECTION 3: ATTENDANCE PROCESSING - Process checkin data efficiently
# ============================================================================

def process_attendance_data(comprehensive_data, target_employees, specific_date=None):
    """
    Process attendance data only for target employees (user + subordinates)
    This avoids processing unnecessary data
    """
    
    # Filter and group checkin data by employee and date
    employee_checkins = defaultdict(lambda: defaultdict(list))
    employees_with_data = set()
    
    for record in comprehensive_data:
        emp_id = record['employee_id']
        
        # Only process if employee is in our target list AND has checkin data
        if emp_id in target_employees and record['has_checkin_data'] == 1:
            employees_with_data.add(emp_id)
            date_str = format_datetime(record['time'], 'yyyy-MM-dd')
            employee_checkins[emp_id][date_str].append({
                'time': record['time'],
                'log_type': record['log_type'],
                'department': record['department'],
                'reports_to': record['reports_to']
            })
    
    # Calculate work hours for each employee
    attendance_results = {}
    
    for emp_id in target_employees:
        if emp_id in employees_with_data:
            # Process attendance data
            emp_attendance = process_employee_attendance(
                employee_checkins[emp_id], 
                emp_id
            )
            attendance_results[emp_id] = emp_attendance
        else:
            # Employee has no attendance data
            attendance_results[emp_id] = {
                'has_attendance': False,
                'daily_data': None,
                'summary': 'No attendance data found'
            }
    
    return {
        'attendance_results': attendance_results,
        'employees_with_data_count': len(employees_with_data),
        'total_target_employees': len(target_employees)
    }


def process_employee_attendance(employee_daily_data, emp_id):
    """
    Process individual employee's attendance data
    Calculate work hours, entry/exit times
    """
    
    daily_summaries = []
    total_work_hours = 0
    total_days = 0
    
    for date_str, logs in employee_daily_data.items():
        # Sort logs by time
        logs = sorted(logs, key=lambda x: x['time'])
        daily_hours = 0.0
        i = 0
        
        # Find IN/OUT pairs and calculate work hours
        while i < len(logs) - 1:
            log1 = logs[i]
            log2 = logs[i + 1]
            
            if log1['log_type'] == "IN" and log2['log_type'] == "OUT":
                shift_hours = time_diff_in_hours(log2['time'], log1['time'])
                daily_hours += round(shift_hours, 2)
                i += 2
            else:
                i += 1
        
        # Prepare daily summary
        entry_time = logs[0]['time'].strftime("%H:%M") if logs else "N/A"
        exit_time = logs[-1]['time'].strftime("%H:%M") if logs else "N/A"
        
        daily_summaries.append({
            'date': date_str,
            'work_hours': daily_hours,
            'entry_time': entry_time,
            'exit_time': exit_time,
            'department': logs[0]['department'] if logs else None
        })
        
        total_work_hours += daily_hours
        total_days += 1
    
    return {
        'has_attendance': True,
        'daily_summaries': daily_summaries,
        'total_work_hours': total_work_hours,
        'total_days': total_days,
        'average_daily_hours': round(total_work_hours / total_days, 2) if total_days > 0 else 0
    }


# ============================================================================
# SECTION 4: ADVANCED CALCULATIONS - Monthly and weekly averages
# ============================================================================

def calculate_period_averages(specific_date, target_employees):
    """
    Calculate monthly and weekly averages for target employees
    More efficient by targeting only required employees
    """
    
    if isinstance(specific_date, str):
        specific_date = datetime.strptime(specific_date, "%Y-%m-%d").date()
    
    # Monthly calculation
    monthly_data = calculate_monthly_averages_optimized(specific_date, target_employees)
    
    # Weekly calculation  
    weekly_data = calculate_weekly_averages_optimized(specific_date, target_employees)
    
    return {
        'monthly_data': monthly_data,
        'weekly_data': weekly_data
    }


def calculate_monthly_averages_optimized(specific_date, target_employees):
    """Optimized monthly average calculation"""
    
    year = specific_date.year
    month = specific_date.month
    today_date = date.today()
    
    # Calculate date range
    first_day = date(year, month, 1)
    if year == today_date.year and month == today_date.month:
        last_day = today_date
    else:
        last_day = date(year, month, calendar.monthrange(year, month)[1])
    
    # Get working days count
    working_days = count_working_days(first_day, last_day)
    
    # Get monthly data for target employees only
    monthly_checkin_data = get_targeted_checkin_data(
        first_day.strftime('%Y-%m-%d'),
        last_day.strftime('%Y-%m-%d'),
        target_employees
    )
    
    if not monthly_checkin_data:
        return {}
    
    # Process monthly data
    monthly_results = {}
    for emp_id in target_employees:
        emp_monthly_data = [record for record in monthly_checkin_data if record['employee'] == emp_id]
        if emp_monthly_data:
            # Calculate monthly statistics
            total_hours = sum(record.get('work_time', 0) for record in emp_monthly_data)
            days_worked = len(emp_monthly_data)
            
            monthly_results[emp_id] = {
                'monthly_average': round(total_hours / working_days, 2) if working_days > 0 else 0,
                'total_hours_worked': total_hours,
                'total_days_worked': days_worked,
                'total_working_days': working_days
            }
    
    return monthly_results


def calculate_weekly_averages_optimized(specific_date, target_employees):
    """Optimized weekly average calculation"""
    
    # Find week boundaries
    days_from_monday = specific_date.weekday()
    week_start = specific_date - timedelta(days=days_from_monday)
    
    today_date = date.today()
    if week_start <= today_date:
        week_end = min(specific_date, week_start + timedelta(days=4))
    else:
        return {}
    
    working_days = count_working_days(week_start, week_end)
    
    # Get weekly data for target employees only
    weekly_checkin_data = get_targeted_checkin_data(
        week_start.strftime('%Y-%m-%d'),
        week_end.strftime('%Y-%m-%d'),
        target_employees
    )
    
    if not weekly_checkin_data:
        return {}
    
    # Process weekly data
    weekly_results = {}
    for emp_id in target_employees:
        emp_weekly_data = [record for record in weekly_checkin_data if record['employee'] == emp_id]
        if emp_weekly_data:
            total_hours = sum(record.get('work_time', 0) for record in emp_weekly_data)
            days_worked = len(emp_weekly_data)
            
            weekly_results[emp_id] = {
                'weekly_average': round(total_hours / working_days, 2) if working_days > 0 else 0,
                'total_hours_worked': total_hours,
                'total_days_worked': days_worked,
                'total_working_days': working_days
            }
    
    return weekly_results


def get_targeted_checkin_data(from_date, to_date, target_employees):
    """Get checkin data only for specific employees - more efficient"""
    
    if not target_employees:
        return []
    
    # Create placeholders for IN clause
    placeholders = ', '.join(['%s'] * len(target_employees))
    
    query = f"""
        SELECT 
            ec.employee,
            DATE(ec.time) as date,
            SUM(
                CASE 
                    WHEN ec.log_type = 'IN' AND LEAD(ec.log_type) OVER (
                        PARTITION BY ec.employee, DATE(ec.time) 
                        ORDER BY ec.time
                    ) = 'OUT' 
                    THEN TIME_TO_SEC(TIMEDIFF(
                        LEAD(ec.time) OVER (
                            PARTITION BY ec.employee, DATE(ec.time) 
                            ORDER BY ec.time
                        ), 
                        ec.time
                    )) / 3600
                    ELSE 0 
                END
            ) as work_time
        FROM `tabEmployee Checkin` ec
        WHERE DATE(ec.time) BETWEEN %s AND %s
        AND ec.employee IN ({placeholders})
        GROUP BY ec.employee, DATE(ec.time)
        ORDER BY ec.employee, DATE(ec.time)
    """
    
    params = [from_date, to_date] + list(target_employees)
    
    try:
        return frappe.db.sql(query, params, as_dict=True)
    except Exception as e:
        frappe.log_error(f"Targeted checkin data error: {str(e)}", "Targeted Data Fetch")
        return []


def count_working_days(start_date, end_date):
    """Count working days between two dates (Monday to Friday)"""
    working_days = 0
    current_date = start_date
    
    while current_date <= end_date:
        if current_date.weekday() < 5:  # Monday=0, Friday=4
            working_days += 1
        current_date += timedelta(days=1)
    
    return working_days


# ============================================================================
# SECTION 5: MAIN ORCHESTRATION FUNCTION - Combines all modules efficiently
# ============================================================================

@frappe.whitelist(allow_guest=True)
def get_optimized_attendance_report(from_date=None, to_date=None, specific_date=None):
    """
    Main function that orchestrates the entire process efficiently
    Combines all modules to minimize database calls and processing
    """
    
    try:
        # Input validation
        if from_date and not to_date:
            return {"error": "Provide To date"}
        if to_date and not from_date:
            return {"error": "Provide From date"}
        if (from_date and specific_date) or (to_date and specific_date):
            return {"error": "Provide either Date range or a single date"}
        
        # Set default date
        if not specific_date and not from_date and not to_date:
            specific_date = today()
        
        # Get current user's employee ID
        user_email = frappe.session.user
        user_emp_id = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
        
        # For testing - remove this line in production
        user_emp_id = "VINOD K"
        
        if not user_emp_id:
            return {"error": "Logged-in user is not linked to an active employee record"}
        
        # STEP 1: Get all comprehensive data in single query
        comprehensive_data = get_comprehensive_data(from_date, to_date, specific_date, user_emp_id)
        
        if isinstance(comprehensive_data, dict) and 'error' in comprehensive_data:
            return comprehensive_data
        
        # STEP 2: Build hierarchy and identify target employees
        hierarchy_info = build_complete_hierarchy(comprehensive_data, user_emp_id)
        
        # Target employees = user + all subordinates
        target_employees = set([user_emp_id] + hierarchy_info['user_subordinates'])
        
        # STEP 3: Process attendance data for target employees only
        attendance_info = process_attendance_data(comprehensive_data, target_employees, specific_date)
        
        # STEP 4: Calculate period averages if single date provided
        period_averages = {}
        if specific_date:
            period_averages = calculate_period_averages(specific_date, target_employees)
        
        # STEP 5: Combine all data into final result
        final_result = {
            'user_info': {
                'employee_id': user_emp_id,
                'total_subordinates': hierarchy_info['total_hierarchy_count']
            },
            'attendance_summary': {
                'total_employees_in_hierarchy': len(target_employees),
                'employees_with_attendance': attendance_info['employees_with_data_count'],
                'employees_without_attendance': len(target_employees) - attendance_info['employees_with_data_count']
            },
            'detailed_attendance': attendance_info['attendance_results'],
            'period_averages': period_averages,
            'hierarchy_map': hierarchy_info['hierarchy_map']
        }
        
        return final_result
        
    except Exception as e:
        frappe.log_error(str(e), "Optimized Attendance Report Error")
        return {"error": f"Unexpected error occurred: {str(e)}"}


# ============================================================================
# SECTION 6: UTILITY FUNCTIONS - Helper functions for debugging and analysis
# ============================================================================

@frappe.whitelist(allow_guest=True)
def debug_data_discrepancy(specific_date=None):
    """
    Debug function to analyze why data counts don't match
    Provides detailed breakdown of data availability
    """
    
    if not specific_date:
        specific_date = today()
    
    user_email = frappe.session.user
    user_emp_id = frappe.db.get_value("Employee", {"user_id": user_email}, "name")
    # user_emp_id = "VINOD K"  # Remove in production
    
    if not user_emp_id:
        return {"error": "User not linked to employee record"}
    
    # Get comprehensive analysis
    comprehensive_data = get_comprehensive_data(None, None, specific_date, user_emp_id)
    hierarchy_info = build_complete_hierarchy(comprehensive_data, user_emp_id)
    
    target_employees = set([user_emp_id] + hierarchy_info['user_subordinates'])
    
    # Analyze data availability
    employees_with_checkin = set()
    employees_without_checkin = set()
    
    for record in comprehensive_data:
        emp_id = record['employee_id']
        if emp_id in target_employees:
            if record['has_checkin_data'] == 1:
                employees_with_checkin.add(emp_id)
            else:
                employees_without_checkin.add(emp_id)
    
    # Remove duplicates
    employees_without_checkin = employees_without_checkin - employees_with_checkin
    
    return {
        'analysis': {
            'total_hierarchy_employees': len(target_employees),
            'employees_with_checkin_data': len(employees_with_checkin),
            'employees_without_checkin_data': len(employees_without_checkin),
            'data_coverage_percentage': round((len(employees_with_checkin) / len(target_employees)) * 100, 2)
        },
        'employees_with_data': sorted(list(employees_with_checkin)),
        'employees_without_data': sorted(list(employees_without_checkin)),
        'recommendations': [
            "Check if employees without data have actually checked in on this date",
            "Verify if employee IDs in hierarchy match those in checkin records",
            "Consider data synchronization issues between Employee and Employee Checkin doctypes"
        ]
    }