import frappe
from frappe.utils import format_datetime, time_diff_in_hours, today
from frappe.utils.data import getdate
from collections import defaultdict
from datetime import datetime, date, timedelta
import calendar


class CheckinDataProcessor:
    """Centralized class for processing checkin data efficiently"""
    def __init__(self):
        self.cache = {}
    
    def get_checkin_data(self, from_date=None, to_date=None, specific_date=None, employee_id=None):
        """Optimized database query with proper indexing and filtering"""
        
        # Build dynamic query conditions
        conditions = []
        params = []
        
        if from_date and to_date:
            conditions.append("DATE(ec.time) BETWEEN %s AND %s")
            params.extend([from_date, to_date])
        elif specific_date:
            conditions.append("DATE(ec.time) = %s")
            params.append(specific_date)
        else:
            frappe.throw("Invalid date parameters")
        
        if employee_id:
            conditions.append("ec.employee = %s")
            params.append(employee_id)
        
        where_clause = " AND ".join(conditions)
        
        # Single optimized query with proper joins
        query = f"""
            SELECT 
                ec.employee,
                ec.time,
                ec.log_type,
                em.department,
                DATE(ec.time) as checkin_date
            FROM
                `tabEmployee Checkin` ec
            INNER JOIN 
                `tabEmployee` em ON ec.employee = em.name
            WHERE {where_clause}
            ORDER BY ec.employee ASC, ec.time ASC
        """
        
        try:
            return frappe.db.sql(query, params, as_dict=True)
        except Exception as e:
            frappe.log_error(f"DB error: {str(e)}", "Check-in Fetch Failure")
            return {"error": "Failed to fetch check-in data"}
    
    def process_checkin_data(self, emp_data):
        """Efficient single-pass processing of checkin data"""
        if not emp_data or isinstance(emp_data, dict):
            return emp_data
        
        # Group and calculate in single pass
        grouped_results = defaultdict(lambda: defaultdict(list))
        final_results = []
        
        # Group by employee and date
        for entry in emp_data:
            date_str = entry['checkin_date']  # Already formatted from query
            grouped_results[entry['employee']][date_str].append(entry)
        
        # Calculate work hours for each employee-date combination
        for emp, days in grouped_results.items():
            for date_str, logs in days.items():
                # Sort logs by time (should already be sorted from query)
                logs.sort(key=lambda x: x['time'])
                
                total_hours = self._calculate_daily_hours(logs)
                
                if logs:  # Ensure we have data
                    final_results.append({
                        "employee": emp,
                        "department": logs[0]['department'],
                        "date": date_str,
                        "work_time": total_hours,
                        "entry": logs[0]['time'].strftime("%H:%M"),
                        "exit": logs[-1]['time'].strftime("%H:%M"),
                        "result_type": "daily_summary"
                    })
        
        return {"result": final_results, "total_count": len(final_results)}
    
    def _calculate_daily_hours(self, logs):
        """Optimized work hours calculation"""
        total_hours = 0.0
        i = 0
        
        while i < len(logs) - 1:
            if logs[i]['log_type'] == "IN" and logs[i + 1]['log_type'] == "OUT":
                hours = time_diff_in_hours(logs[i + 1]['time'], logs[i]['time'])
                total_hours += round(hours, 2)
                i += 2
            else:
                i += 1
        
        return total_hours
    
    def calculate_period_averages(self, daily_data, period_type='monthly', working_days=None):
        """Generic function for calculating averages (monthly/weekly)"""
        if not daily_data or 'result' not in daily_data:
            return {"result": [], "total_count": 0}
        
        summary_data = defaultdict(lambda: {
            'total_work_hours': 0.0,
            'days': 0,
            'department': None
        })
        
        # Single pass aggregation
        for record in daily_data['result']:
            emp_name = record['employee']
            summary_data[emp_name]['total_work_hours'] += record['work_time']
            summary_data[emp_name]['days'] += 1
            summary_data[emp_name]['department'] = record['department']
        
        # Build results
        result = []
        for emp_name, stats in summary_data.items():
            avg_hours = round(stats['total_work_hours'] / working_days, 2) if working_days else 0
            
            result.append({
                "employee": emp_name,
                "department": stats['department'] or "Unknown",
                f"{period_type}_average": avg_hours,
                "total_hours_worked": stats['total_work_hours'],
                "total_days_worked": stats['days'],
                f"total_{period_type}_working_days": working_days,
                "result_type": f"{period_type}_average"
            })
        
        return {"result": result, "total_count": len(result)}


# Utility functions
def get_working_days(start_date, end_date):
    """Optimized working days calculation"""
    if isinstance(start_date, str):
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    if isinstance(end_date, str):
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    
    total_days = (end_date - start_date).days + 1
    weeks = total_days // 7
    remaining_days = total_days % 7
    
    # Calculate working days efficiently
    working_days = weeks * 5  # 5 working days per week
    
    # Add remaining weekdays
    current_date = start_date + timedelta(days=weeks * 7)
    for _ in range(remaining_days):
        if current_date.weekday() < 5:
            working_days += 1
        current_date += timedelta(days=1)
    
    return working_days


def get_week_boundaries(specific_date):
    """Get week start (Monday) and end based on given date"""
    if isinstance(specific_date, str):
        specific_date = datetime.strptime(specific_date, "%Y-%m-%d").date()
    
    # Find Monday of the week
    days_from_monday = specific_date.weekday()
    week_start = specific_date - timedelta(days=days_from_monday)
    
    # Determine week end
    today_date = date.today()
    today_monday = today_date - timedelta(days=today_date.weekday())
    
    if week_start == today_monday:
        week_end = specific_date
    elif week_start < today_monday:
        week_end = week_start + timedelta(days=4)  # Friday
    else:
        raise ValueError("Invalid future date")
    
    return week_start, week_end


def get_month_boundaries(specific_date):
    """Get month start and end based on given date"""
    if isinstance(specific_date, str):
        specific_date = datetime.strptime(specific_date, "%Y-%m-%d").date()
    
    year, month = specific_date.year, specific_date.month
    today_date = date.today()
    
    first_day = date(year, month, 1)
    
    if year == today_date.year and month == today_date.month:
        last_day = today_date
    elif specific_date < today_date:
        last_day = date(year, month, calendar.monthrange(year, month)[1])
    else:
        raise ValueError("Invalid future date")
    
    return first_day, last_day


class EmployeeRegistry:
    """Efficient employee data management"""
    
    def __init__(self):
        self.employees = {}
    
    def add_employees_from_data(self, data_list, data_type):
        """Add employees from various data sources"""
        if not data_list or 'result' not in data_list:
            return
        
        for record in data_list['result']:
            emp_name = record['employee']
            
            if emp_name not in self.employees:
                self.employees[emp_name] = {
                    'employee_info': {
                        'name': emp_name,
                        'department': record.get('department', 'Unknown')
                    },
                    'daily_data': None,
                    'monthly_data': None,
                    'weekly_data': None
                }
            
            # Add specific data type
            if data_type == 'daily':
                self.employees[emp_name]['daily_data'] = {
                    'date': record['date'],
                    'work_hours': record['work_time'],
                    'entry_time': record['entry'],
                    'exit_time': record['exit'],
                }
            elif data_type == 'monthly':
                self.employees[emp_name]['monthly_data'] = {
                    'average_work_hours': record['monthly_average'],
                    'total_hours_worked': record['total_hours_worked'],
                    'total_days_worked': record['total_days_worked'],
                    'total_working_days': record['total_monthly_working_days']
                }
            elif data_type == 'weekly':
                self.employees[emp_name]['weekly_data'] = {
                    'average_work_hours': record['weekly_average'],
                    'total_hours_worked': record['total_hours_worked'],
                    'total_days_worked': record['total_days_worked'],
                    'total_working_days': record['total_weekly_working_days']
                }
    
    def get_registry(self):
        return self.employees


@frappe.whitelist(allow_guest=True)
def fetch_checkins(from_date=None, to_date=None, specific_date=None, employee_id=None):
    """Optimized main function with better error handling and efficiency"""
    try:
        # Input validation
        if from_date and not to_date:
            return {"error": "Please provide both from_date and to_date"}
        if to_date and not from_date:
            return {"error": "Please provide both from_date and to_date"}
        if (from_date and specific_date) or (to_date and specific_date):
            return {"error": "Provide either date range or single date, not both"}
        
        # Default to today if no date provided
        if not any([specific_date, from_date, to_date]):
            specific_date = today()
        
        processor = CheckinDataProcessor()
        
        # Handle date range queries
        if from_date and to_date:
            emp_data = processor.get_checkin_data(from_date, to_date, employee_id=employee_id)
            if isinstance(emp_data, dict) and 'error' in emp_data:
                return emp_data
            
            processed_data = processor.process_checkin_data(emp_data)
            working_days = get_working_days(from_date, to_date)
            
            return processor.calculate_period_averages(processed_data, 'daterange', working_days)
        
        # Handle specific date queries
        elif specific_date:
            # Get all data in parallel concept (though Python is single-threaded)
            results = {}
            
            # Daily data
            daily_data = processor.get_checkin_data(specific_date=specific_date, employee_id=employee_id)
            if not isinstance(daily_data, dict) or 'error' not in daily_data:
                results['daily'] = processor.process_checkin_data(daily_data)
            
            # Monthly data
            try:
                month_start, month_end = get_month_boundaries(specific_date)
                monthly_data = processor.get_checkin_data(
                    from_date=month_start.strftime('%Y-%m-%d'),
                    to_date=month_end.strftime('%Y-%m-%d'),
                    employee_id=employee_id
                )
                if not isinstance(monthly_data, dict) or 'error' not in monthly_data:
                    monthly_processed = processor.process_checkin_data(monthly_data)
                    monthly_working_days = get_working_days(month_start, month_end)
                    results['monthly'] = processor.calculate_period_averages(
                        monthly_processed, 'monthly', monthly_working_days
                    )
            except ValueError as e:
                results['monthly'] = {"error": str(e)}
            
            # Weekly data
            try:
                week_start, week_end = get_week_boundaries(specific_date)
                weekly_data = processor.get_checkin_data(
                    from_date=week_start.strftime('%Y-%m-%d'),
                    to_date=week_end.strftime('%Y-%m-%d'),
                    employee_id=employee_id
                )
                if not isinstance(weekly_data, dict) or 'error' not in weekly_data:
                    weekly_processed = processor.process_checkin_data(weekly_data)
                    weekly_working_days = get_working_days(week_start, week_end)
                    results['weekly'] = processor.calculate_period_averages(
                        weekly_processed, 'weekly', weekly_working_days
                    )
            except ValueError as e:
                results['weekly'] = {"error": str(e)}
            
            # Combine all data efficiently
            registry = EmployeeRegistry()
            
            for data_type, data in results.items():
                if isinstance(data, dict) and 'error' not in data:
                    registry.add_employees_from_data(data, data_type)
            
            employee_registry = registry.get_registry()
            return {
                "employees": employee_registry,
                "length": len(employee_registry)
            }
    
    except Exception as e:
        frappe.log_error(str(e), "Unexpected error in fetch_checkins")
        return {"error": "An unexpected error occurred"}


# Additional utility functions for backward compatibility
@frappe.whitelist(allow_guest=True)
def get_monthly_average(specific_date):
    """Standalone monthly average function"""
    try:
        month_start, month_end = get_month_boundaries(specific_date)
        working_days = get_working_days(month_start, month_end)
        
        processor = CheckinDataProcessor()
        data = processor.get_checkin_data(
            from_date=month_start.strftime('%Y-%m-%d'),
            to_date=month_end.strftime('%Y-%m-%d')
        )
        
        if isinstance(data, dict) and 'error' in data:
            return data
        
        processed = processor.process_checkin_data(data)
        return processor.calculate_period_averages(processed, 'monthly', working_days)
        
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        frappe.log_error(str(e), "Error in get_monthly_average")
        return {"error": "Failed to calculate monthly average"}


@frappe.whitelist(allow_guest=True)
def get_weekly_average(specific_date):
    """Standalone weekly average function"""
    try:
        week_start, week_end = get_week_boundaries(specific_date)
        working_days = get_working_days(week_start, week_end)
        
        processor = CheckinDataProcessor()
        data = processor.get_checkin_data(
            from_date=week_start.strftime('%Y-%m-%d'),
            to_date=week_end.strftime('%Y-%m-%d')
        )
        
        if isinstance(data, dict) and 'error' in data:
            return data
        
        processed = processor.process_checkin_data(data)
        return processor.calculate_period_averages(processed, 'weekly', working_days)
        
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        frappe.log_error(str(e), "Error in get_weekly_average")
        return {"error": "Failed to calculate weekly average"}