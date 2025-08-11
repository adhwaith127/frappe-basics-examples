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
        
        working_days = 0  #for counting total working days in the between dates
        current_date = start_date   #set inital date as start date
        while current_date <= end_date:   #when current date(the currently selected date) is less than ending date 
            if current_date.weekday() < 5:     #increment working days count +1
                working_days += 1
            current_date += timedelta(days=1)   #after incrementing working day count move to next day by incrementing day+1
        return working_days
    
    except (TypeError, ValueError) as e:
        frappe.log_error(f"Working days error | Start: {start_date} | End: {end_date} | Error: {str(e)}", "Working_Days_Error")
        return 0
    except Exception as e:
        frappe.log_error(f"Unexpected working days error | Error: {str(e)}", "Working_Days_Unexpected")
        return 0


# calculates total work hours from a sorted list of daily check-in logs.
def _calculate_daily_work_hours(logs):
    total_hours = 0.      # total daily working time
    last_in_time = None   # Used to remember the last time the person checked IN
    first_in_time=None
    last_out_time=None
    
    if not logs:
        return {
            "employee": None,"department": None,"reports_to": None,
            "date": None,"work_time": 0.0,"entry_time": None,
            "exit_time": None,"status": "absent"
        }
    
    # logs.sort(key=lambda x:x['time'])      <-- essentially redundant since sql gives sorted data already

    for log in logs:
        if log['log_type'] == "IN" and last_in_time is None:
            last_in_time = log['time']
            # first_in_time=log['time']
        elif log['log_type'] == "OUT" :
            if last_in_time is not None:
                total_hours += time_diff_in_hours(log['time'], last_in_time)  # calculate difference between IN and OUT
                last_in_time = None          # reset last_in_time to None so we can track the next IN/OUT pair
            last_out_time=log['time'].strftime("%H:%M")

    first_log_time = logs[0]['time'].strftime("%H:%M")   # finding first and last logs of the day
    # last_log_time = logs[-1]['time'].strftime("%H:%M")
    last_log_time=last_out_time

    return {
        "employee": logs[0]['employee'],
        "department": logs[0]['department'],
        "reports_to": logs[0]['reports_to'],
        "date": format_datetime(logs[0]['time'], 'yyyy-MM-dd'),
        "work_time": round(total_hours, 2),
        "entry_time": first_log_time,
        "exit_time": last_log_time,
        "status": "present"
    }

# Fetches, groups, and processes check-in data for a given date range.
def get_processed_checkin_data(from_date, to_date):
    try:
        if not from_date or not to_date:
            return []
        
        date_condition = "DATE(ec.time) BETWEEN %(from_date)s AND %(to_date)s"   #setting date condition to fetch data between those dates
        query = f"""                                        
            SELECT                                          -- building a base query that we can use to fetch data
                ec.employee, ec.time, ec.log_type,          -- from employee checkin select employee,log time,log type(in or out)
                em.department, em.reports_to                -- from employee table getting department of employee and who they reports to 
            FROM `tabEmployee Checkin` AS ec
            JOIN `tabEmployee` AS em ON ec.employee = em.name    -- matching both tables for join with each tables field with name as matching(use id later)
            WHERE {date_condition} AND em.status = 'Active'        -- setting date inside where and selecting employees who are active
            ORDER BY ec.employee, ec.time                   -- order data by employee name first then inside that sort by log time 
        """               
        params = {"from_date": from_date, "to_date": to_date}   #pass dates for date condition above
        
        raw_data = frappe.db.sql(query, params, as_dict=True)    # set result inside raw_data variable
        if not raw_data:                                   
            return []
    
        grouped_data = defaultdict(lambda: defaultdict(list))     # creating a default dict. this when called with an entry like this ['x']
        for entry in raw_data:                                    # will create a new list with that name (x)
            date_str = format_datetime(entry['time'], 'yyyy-MM-dd')  # Convert the check-in time to a date string
            grouped_data[entry['employee']][date_str].append(entry)  # Group the entry under the employee's ID and the specific date
            # syntax of what this does --> # { emp : { date : [] } } # If the employee or date key doesn't exist, they are automatically created
            # example output : {'EMP001': {'YYYY-MM-DD': [entry1, entry2, ...],....}

        daily_summaries = []
        for employee, days in grouped_data.items():  # separate employee name(key) and date(value) 
            for date_key, logs in days.items():      # separate date and logs(in data,out data,in , out......)
                if logs:
                    daily_summary = _calculate_daily_work_hours(logs)
                    daily_summaries.append(daily_summary)
        
        return daily_summaries
    except Exception as e:
        frappe.log_error(f"Checkin data processing error | From: {from_date} | To: {to_date} | Error: {str(e)}", "Checkin_Processing_Error")
        return []


# function to create a summary from a list of daily processed records.
def _create_period_summary(daily_records, total_working_days, result_type):
    summary_data = defaultdict(lambda: {
        'total_work_hours': 0.0, 'days_worked': 0,
        'department': None, 'reports_to': None
    })

    for record in daily_records:                   # myb multiple dates data for each employee ??
        emp = summary_data[record['employee']]
        emp['total_work_hours'] += record['work_time']
        emp['days_worked'] += 1
        emp['department'] = record['department']
        emp['reports_to'] = record['reports_to']
    
    result_list = []
    for emp_name, stats in summary_data.items():    # key is emp name and value is {'','','',''} with fields said above
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
# If an employee from the attendance data exists in the registry, their record for the given data_key is updated.
def _populate_registry(registry, data, data_key):
    for record in data:
        emp_name = record['employee']
        
        if emp_name in registry:
            record_copy = record.copy()
            # removing unnecessary fields
            record_copy.pop('employee', None)
            record_copy.pop('department', None)
            record_copy.pop('reports_to', None)
            record_copy.pop('result_type', None)            
            # registry--> emp name --> data_key(daily,weekly,monthly)--> add data 
            registry[emp_name][data_key] = record_copy

    return registry


# Builds a map of manager to their direct reports(a hierarchy list) 
def get_hierarchy_map():
    try:    
        employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "reports_to"])  # list of dicts
        if not employees:
            return defaultdict(list)
        
        hierarchy = defaultdict(list)
        for emp in employees:    # for each employee in employee list create a hierarchy tree
            if emp.reports_to:
                hierarchy[emp.reports_to].append(emp.name)
        return hierarchy

    except Exception as e:
        frappe.log_error(f"Hierarchy map error | Error: {str(e)}", "Hierarchy_Error")
        return defaultdict(list)

# finds all direct and indirect subordinates for a given manager (uses iteration, not actual recursion)(cgpt)
def get_all_subordinates(manager_id, hierarchy_map):
    try:
        if not manager_id or not hierarchy_map:               # <--- this method uses BFS
            return []
        
        all_subs = set()
        queue = hierarchy_map.get(manager_id, [])   
        visited = set(queue)
        all_subs.update(queue)

        while queue:  #moves one by one.pops first(removes and store).check subs and appends them to end. 
            current_manager = queue.pop(0)  # always removing first.so not actually same item always
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

# Structures the pre-filtered employee registry into a final format with manager data at the top level and subordinates nested.
def _structure_data_for_hierarchy(employee_registry, manager_id, subordinate_ids):

    manager_data = employee_registry.get(manager_id, {})
    subordinates_data = {
        emp_id: data # add the employees data if below conditions match (for and if written in reverse)
        for emp_id, data in employee_registry.items() 
        if emp_id in subordinate_ids       # idea--->  If it's not true, nothing happens — it just skips that pair.
    }
    return {
        "user_id":manager_id,
        "manager_data": manager_data,
        "subordinates_data": subordinates_data,
        "total_count(with manager)": len(subordinates_data) + (1 if manager_data else 0)
    }

# --- Main API Function ---

@frappe.whitelist()
def fetch_checkins(from_date=None, to_date=None, specific_date=None):
    try:
        # --- 1. Input Validation and Date Setup ---
        if from_date and not to_date: frappe.throw("Please provide a 'To Date' for the date range.")
        if to_date and not from_date: frappe.throw("Please provide a 'From Date' for the date range.")
        if (from_date or to_date) and specific_date: frappe.throw("Provide either a date range or a specific date, not both.")
        
        if not (from_date or to_date or specific_date):
            specific_date = today()
            
        # --- 2. Handle Date Range Request ---
        if from_date and to_date:
            try:
                start, end = getdate(from_date), getdate(to_date)
                processed_data = get_processed_checkin_data(start, end)
                if not processed_data:    # getting [] as result would make this condition truth and returns this error
                    return {"message": f"No check-in data found between {from_date} and {to_date}."}
                
                working_days = _get_working_days(start, end)
                return _create_period_summary(processed_data, working_days, "daterange_summary")

            except Exception as e:
                frappe.log_error(f"Date range processing error | From: {from_date} | To: {to_date} | Error: {str(e)}", "Daterange_Error")
                return {"error": "Failed to process date range request"}

        # --- 3. Handle Specific Date Request ---
        elif specific_date:
            try:
                s_date = getdate(specific_date)   # convert to datetime
                if s_date > getdate(today()):    # if selected date(datetime.date object) greater than today 
                    return {"error": "Cannot fetch data for a future date."}
                
                # --- 3a. Get user hierarchy first to build a complete employee list ---

                if frappe.session.user == 'Administrator':
                    user_id="administrator"
                    employee_details=frappe.get_all("Employee",filters=[['status','=','Active']],fields=["name",'department','reports_to'])
                else:
                    user_id=frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
                    if not user_id:
                        frappe.throw("Logged-in user is not linked to an active employee record.")
                    
                    hierarchy = get_hierarchy_map()
                    subordinates = get_all_subordinates(user_id, hierarchy)
                    allowed_employees = set(subordinates + [user_id])

                    employee_details = frappe.get_all("Employee",           # get details of manager and subordinates
                        filters={"name": ["in", list(allowed_employees)]},
                        fields=["name", "department", "reports_to"]
                    )

                print("#"*50)                  # testing !!!!
                print(frappe.session.user)     # testing !!!!
                
                # --- 3b. Pre-build registry with all employees in the hierarchy ---
                # Attendance data keys are initialized as empty dicts.
                employee_registry = {
                    emp.name: {
                        'employee_info': {
                            'name': emp.name, 'department': emp.department, 'reports_to': emp.reports_to
                        },
                        'daily_data': {}, 'weekly_summary': {}, 'monthly_summary': {}
                    } for emp in employee_details
                }
                
                # --- 3c. Fetch and process attendance data for the period ---
                month_start = s_date.replace(day=1)
                week_start = s_date - timedelta(days=s_date.weekday())   #get weekday(0-6).set days= that.then subtract
                
                # min returns the smallest (or "minimum") value from the inputs you give it.
                week_end = min(s_date, week_start + timedelta(days=4))  # check if given date is smaller than weekend!!
                
                all_processed_data = get_processed_checkin_data(month_start, s_date)
                
                # populating registry. calculated separately to avoid missing summary
                
                # calculate working days altogether
                working_day_counts={
                    "monthly":_get_working_days(month_start,s_date),
                    "weekly":_get_working_days(week_start,week_end),
                }

                if all_processed_data:         # calculate monthly
                    # monthly_working_days = _get_working_days(month_start, s_date)
                    monthly_summary = _create_period_summary(all_processed_data, working_day_counts["monthly"], "monthly_summary")
                    _populate_registry(employee_registry, monthly_summary, 'monthly_summary')
                    
                if all_processed_data:         # calculate weekly
                    weekly_data_list = [d for d in all_processed_data if week_start <= getdate(d['date']) <= week_end]  # for all between range
                    if weekly_data_list:
                        # weekly_working_days = _get_working_days(week_start, week_end)
                        weekly_summary = _create_period_summary(weekly_data_list, working_day_counts["weekly"], "weekly_summary")
                        _populate_registry(employee_registry, weekly_summary, 'weekly_summary')
                    
                if all_processed_data:         # calculate daily
                    daily_data = [d for d in all_processed_data if getdate(d['date']) == s_date]  # for each data in apd if date == given add to []
                    if daily_data:
                        _populate_registry(employee_registry, daily_data, 'daily_data')
                
                for emp_name in employee_registry.keys():  # handle absent employees
                    if not employee_registry[emp_name]['daily_data']:
                        employee_registry[emp_name]['daily_data'] = {
                            'date': format_datetime(s_date, 'yyyy-MM-dd'),
                            'work_time': 0.0,
                            'entry_time': None, 
                            'exit_time': None,
                            'status': 'absent'
                        }
                
                # final output
                if frappe.session.user=="Administrator":
                    subordinates=[emp.name for emp in employee_details if emp.name!=user_id]
                
                return _structure_data_for_hierarchy(employee_registry, user_id, subordinates)

            except Exception as e:
                frappe.log_error(f"Specific date error | User: {frappe.session.user} | Date: {specific_date} | Error: {str(e)}", "Specific_Date_Error")
                return {"error": "Failed to process request"}

    except Exception as e:
        frappe.log_error(f"Critical error | User: {frappe.session.user} | Params: {from_date}, {to_date}, {specific_date} | Error: {str(e)}", "Critical_Error")
        return {"error": "System error occurred."}