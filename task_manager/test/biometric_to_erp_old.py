import frappe
from frappe.utils import get_datetime, getdate, time_diff_in_seconds
from frappe import _
from frappe.model.naming import make_autoname
from datetime import datetime, timedelta, time
import pytz


@frappe.whitelist(allow_guest=True)
def add_checkin(punchingcode, employee_name, time, device_id):
    """
    Main API function that routes attendance logs to appropriate tables
    based on device mapping configuration
    """
    try:
        # Step 1: Get employee by biometric ID
        employee = frappe.db.get_value(
            "Employee",
            {"attendance_device_id": punchingcode},
            ["name", "employee_name"]
        )

        if not employee:
            error_msg = f"No Employee found for Biometric ID: {punchingcode}"
            frappe.log_error(error_msg, "Employee Not Found")
            frappe.throw(_(error_msg))

        employee_id, full_name = employee
        
        try:
            checkin_time = get_datetime(time)
            checkin_date = getdate(checkin_time)
        except Exception as e:
            error_msg = f"Invalid time format received: {time}"
            frappe.log_error(f"{error_msg} - {str(e)}", "Time Format Error")
            frappe.throw(_(error_msg))

        # Step 2: Get device mapping to determine target table
        device_mapping = get_device_mapping(device_id)
        
        if not device_mapping:
            # Default to Employee Checkin if no mapping found
            frappe.log_error(f"No device mapping found for device_id: {device_id}. Using default Employee Checkin.", "Device Mapping Warning")
            return handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, None)
        
        target_table = device_mapping.get('database_table')
        location = device_mapping.get('location')

        # Step 3: Clean target table name (remove backticks) and route to appropriate handler
        clean_target_table = target_table.strip('`').strip()
        
        if clean_target_table == 'tabEmployee Checkin':
            return handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location)
        elif clean_target_table == 'tabOvertime Checkin':
            return handle_overtime_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location)
        elif clean_target_table == 'tabSecurity Gate Checkin':
            return handle_security_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location)
        else:
            error_msg = f"Invalid database table configuration: {target_table}. Cleaned value: {clean_target_table}"
            frappe.log_error(error_msg, "Invalid Table Configuration")
            frappe.throw(_(error_msg))

    except frappe.DoesNotExistError as e:
        error_msg = f"Database record not found: {str(e)}"
        frappe.log_error(f"Error in add_checkin for {punchingcode}: {error_msg}", "Database Error")
        frappe.throw(_(error_msg))
    except frappe.ValidationError as e:
        error_msg = f"Validation error: {str(e)}"
        frappe.log_error(f"Error in add_checkin for {punchingcode}: {error_msg}", "Validation Error")
        frappe.throw(_(error_msg))
    except Exception as e:
        error_msg = f"Unexpected error processing checkin: {str(e)}"
        frappe.log_error(f"Error in add_checkin for {punchingcode}: {error_msg}", "Biometric API Error")
        frappe.throw(_(error_msg))


def get_device_mapping(device_id):
    """
    Get device mapping configuration from Biometric Device Mapping doctype
    """
    try:
        device_doc = frappe.get_single("Biometric Device Mapping")
        
        # Search in the child table for matching serial number
        for row in device_doc.table_sgvh:
            if row.serial_number == device_id:
                return {
                    'database_table': row.database_table,
                    'location': row.location
                }
        return None
        
    except frappe.DoesNotExistError:
        frappe.log_error(f"Biometric Device Mapping doctype not found", "Device Mapping Error")
        return None
    except Exception as e:
        frappe.log_error(f"Error fetching device mapping for {device_id}: {str(e)}", "Device Mapping Error")
        return None


def handle_employee_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location):
    """
    Handle traditional Employee Checkin - creates new row for each punch
    """
    try:
        # Determine log_type by checking last entry for the day
        log_type = "IN"
        last_log_type = frappe.db.sql(
            """
            SELECT log_type FROM `tabEmployee Checkin`
            WHERE employee = %s AND DATE(time) = %s
            ORDER BY time DESC LIMIT 1
            """,
            (employee_id, checkin_date),
            as_dict=False
        )

        if last_log_type:
            log_type = "OUT" if last_log_type[0][0] == "IN" else "IN"

        # Generate name using naming series
        name = make_autoname('CHKIN-.#####')

        # Insert new checkin record
        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)
        
        frappe.db.sql("""
            INSERT INTO `tabEmployee Checkin`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             employee, employee_name, time, device_id, log_type, custom_device_location)
            VALUES (%s,%s, %s, %s, %s, 0, 0,
             %s, %s, %s, %s, %s, %s)
        """, (
            name, current_ist, current_ist, frappe.session.user, frappe.session.user,
            employee_id, full_name, checkin_time, device_id, log_type, location
        ))

        frappe.db.commit()

        return {
            "status": "success",
            "name": name,
            "log_type": log_type,
            "checkin_time": checkin_time,
            "table": "Employee Checkin"
        }
        
    except Exception as e:
        frappe.db.rollback()
        error_msg = f"Failed to create Employee Checkin record: {str(e)}"
        frappe.log_error(f"Employee Checkin Error for {employee_id}: {error_msg}", "Employee Checkin Error")
        frappe.throw(_(error_msg))


def handle_overtime_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location):
    """
    Handle Overtime Checkin - one row per day with child table for sessions
    """
    try:
        # Check if record exists for today
        existing_record = frappe.db.get_value(
            "Overtime Checkin",
            {"employee": employee_id, "date": checkin_date},
            ["name", "total_overtime"]
        )

        if existing_record:
            # Update existing record
            return update_overtime_session(existing_record[0], employee_id, checkin_time, checkin_date)
        else:
            # Create new record for today
            return create_new_overtime_record(employee_id, full_name, checkin_time, checkin_date, device_id, location)
            
    except Exception as e:
        error_msg = f"Failed to handle overtime checkin: {str(e)}"
        frappe.log_error(f"Overtime Checkin Error for {employee_id}: {error_msg}", "Overtime Checkin Error")
        frappe.throw(_(error_msg))


def handle_security_checkin(employee_id, full_name, checkin_time, checkin_date, device_id, location):
    """
    Handle Security Gate Checkin - one row per day with child table for sessions
    """
    try:
        # Check if record exists for today
        existing_record = frappe.db.get_value(
            "Security Gate Checkin",
            {"employee": employee_id, "date": checkin_date},
            ["name", "total_session_time"]
        )

        if existing_record:
            # Update existing record
            return update_security_session(existing_record[0], employee_id, checkin_time, checkin_date)
        else:
            # Create new record for today
            return create_new_security_record(employee_id, full_name, checkin_time, checkin_date, device_id, location)
            
    except Exception as e:
        error_msg = f"Failed to handle security checkin: {str(e)}"
        frappe.log_error(f"Security Checkin Error for {employee_id}: {error_msg}", "Security Checkin Error")
        frappe.throw(_(error_msg))


def create_new_overtime_record(employee_id, full_name, checkin_time, checkin_date, device_id, location):
    """
    Create new Overtime Checkin record for the day
    """
    try:
        name = make_autoname('OT-.#####')
        log_type = "IN"  # First entry is always IN
        
        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)
        
        # Insert main record
        frappe.db.sql("""
            INSERT INTO `tabOvertime Checkin`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             employee, employee_name, date, total_overtime, device_id)
            VALUES (%s, %s, %s, %s, %s, 0, 0,
             %s, %s, %s, %s, %s)
        """, (
            name, current_ist, current_ist, frappe.session.user, frappe.session.user,
            employee_id, full_name, checkin_date, 0, device_id
        ))

        # Insert child record for the session
        child_name = make_autoname('OTS-.#####')
        frappe.db.sql("""
            INSERT INTO `tabOvertime Sessions`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             parent, parentfield, parenttype, time, log_type)
            VALUES (%s, %s, %s, %s, %s, 0, 1,
             %s, 'overtime_sessions', 'Overtime Checkin', %s, %s)
        """, (
            child_name, current_ist, current_ist, frappe.session.user, frappe.session.user,
            name, checkin_time.time(), log_type
        ))

        frappe.db.commit()

        return {
            "status": "success",
            "name": name,
            "log_type": log_type,
            "checkin_time": checkin_time,
            "table": "Overtime Checkin"
        }
        
    except Exception as e:
        frappe.db.rollback()
        error_msg = f"Failed to create new overtime record: {str(e)}"
        frappe.log_error(f"Create Overtime Record Error for {employee_id}: {error_msg}", "Overtime Creation Error")
        raise Exception(error_msg)


def create_new_security_record(employee_id, full_name, checkin_time, checkin_date, device_id, location):
    """
    Create new Security Gate Checkin record for the day
    """
    try:
        name = make_autoname('SEC-.#####')
        log_type = "IN"  # First entry is always IN
        
        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)
        
        # Insert main record
        frappe.db.sql("""
            INSERT INTO `tabSecurity Gate Checkin`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             employee, employee_name, date, total_session_time, device_id)
            VALUES (%s, %s, %s, %s, %s, 0, 0,
             %s, %s, %s, %s, %s)
        """, (
            name, current_ist, current_ist, frappe.session.user, frappe.session.user,
            employee_id, full_name, checkin_date, 0, device_id
        ))

        # Insert child record for the session
        child_name = make_autoname('SECS-.#####')
        frappe.db.sql("""
            INSERT INTO `tabSecurity Gate Sessions`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             parent, parentfield, parenttype, time, log_type)
            VALUES (%s, %s, %s, %s, %s, 0, 1,
             %s, 'security_sessions', 'Security Gate Checkin', %s, %s)
        """, (
            child_name, current_ist, current_ist, frappe.session.user, frappe.session.user,
            name, checkin_time.time(), log_type
        ))

        frappe.db.commit()

        return {
            "status": "success",
            "name": name,
            "log_type": log_type,
            "checkin_time": checkin_time,
            "table": "Security Gate Checkin"
        }
        
    except Exception as e:
        frappe.db.rollback()
        error_msg = f"Failed to create new security record: {str(e)}"
        frappe.log_error(f"Create Security Record Error for {employee_id}: {error_msg}", "Security Creation Error")
        raise Exception(error_msg)


def update_overtime_session(record_name, employee_id, checkin_time, checkin_date):
    """
    Add new session to existing overtime record
    """
    try:
        # Get last log type for this record
        last_session = frappe.db.sql("""
            SELECT log_type, time FROM `tabOvertime Sessions`
            WHERE parent = %s
            ORDER BY idx DESC LIMIT 1
        """, (record_name,), as_dict=True)

        log_type = "OUT" if (last_session and last_session[0]['log_type'] == "IN") else "IN"
        
        # Get next idx
        max_idx = frappe.db.sql("""
            SELECT COALESCE(MAX(idx), 0) + 1 as next_idx
            FROM `tabOvertime Sessions`
            WHERE parent = %s
        """, (record_name,), as_dict=True)[0]['next_idx']

        # Insert new session
        child_name = make_autoname('OTS-.#####')
        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)
        
        frappe.db.sql("""
            INSERT INTO `tabOvertime Sessions`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             parent, parentfield, parenttype, time, log_type)
            VALUES (%s, %s, %s, %s, %s, 0, %s,
             %s, 'overtime_sessions', 'Overtime Checkin', %s, %s)
        """, (
            child_name, current_ist, current_ist, frappe.session.user, frappe.session.user, max_idx,
            record_name, checkin_time.time(), log_type
        ))

        # Calculate and update total overtime if this is an OUT
        if log_type == "OUT" and last_session:
            calculate_and_update_overtime_total(record_name, last_session[0]['time'], checkin_time.time(), child_name)

        frappe.db.commit()

        return {
            "status": "success",
            "name": record_name,
            "log_type": log_type,
            "checkin_time": checkin_time,
            "table": "Overtime Checkin"
        }
        
    except Exception as e:
        frappe.db.rollback()
        error_msg = f"Failed to update overtime session: {str(e)}"
        frappe.log_error(f"Update Overtime Session Error for {employee_id}: {error_msg}", "Overtime Update Error")
        raise Exception(error_msg)


def update_security_session(record_name, employee_id, checkin_time, checkin_date):
    """
    Add new session to existing security record
    """
    try:
        # Get last log type for this record
        last_session = frappe.db.sql("""
            SELECT log_type, time FROM `tabSecurity Gate Sessions`
            WHERE parent = %s
            ORDER BY idx DESC LIMIT 1
        """, (record_name,), as_dict=True)

        log_type = "OUT" if (last_session and last_session[0]['log_type'] == "IN") else "IN"
        
        # Get next idx
        max_idx = frappe.db.sql("""
            SELECT COALESCE(MAX(idx), 0) + 1 as next_idx
            FROM `tabSecurity Gate Sessions`
            WHERE parent = %s
        """, (record_name,), as_dict=True)[0]['next_idx']

        # Insert new session
        child_name = make_autoname('SECS-.#####')
        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)
        
        frappe.db.sql("""
            INSERT INTO `tabSecurity Gate Sessions`
            (name, creation, modified, modified_by, owner, docstatus, idx,
             parent, parentfield, parenttype, time, log_type)
            VALUES (%s, %s, %s, %s, %s, 0, %s,
             %s, 'security_gate_sessions', 'Security Gate Checkin', %s, %s)
        """, (
            child_name, current_ist, current_ist, frappe.session.user, frappe.session.user, max_idx,
            record_name, checkin_time.time(), log_type
        ))

        # Calculate and update total session time if this is an OUT
        if log_type == "OUT" and last_session:
            calculate_and_update_security_total(record_name, last_session[0]['time'], checkin_time.time())

        frappe.db.commit()

        return {
            "status": "success",
            "name": record_name,
            "log_type": log_type,
            "checkin_time": checkin_time,
            "table": "Security Gate Checkin"
        }
        
    except Exception as e:
        frappe.db.rollback()
        error_msg = f"Failed to update security session: {str(e)}"
        frappe.log_error(f"Update Security Session Error for {employee_id}: {error_msg}", "Security Update Error")
        raise Exception(error_msg)


def calculate_and_update_overtime_total(record_name, in_time, out_time, child_name):
    """
    Calculate session duration and update total overtime
    """
    # --- helper to normalize any input into datetime.time ---
    def _normalize_to_time(val):
        if isinstance(val, time):
            return val  # already correct
        if isinstance(val, str):
            # Convert "HH:MM:SS" string to time
            h, m, *s = map(int, val.split(":"))
            return time(h, m, s[0] if s else 0)
        if isinstance(val, timedelta):
            # Convert timedelta to clock time (wrap at 24h)
            total_seconds = int(val.total_seconds()) % (24 * 3600)
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            s = total_seconds % 60
            return time(h, m, s)
        raise TypeError(f"Unsupported type for overtime input: {type(val)}")

    try:
        today = datetime.now().date()

        # normalize both inputs before combine()
        in_time = _normalize_to_time(in_time)
        out_time = _normalize_to_time(out_time)

        in_datetime = datetime.combine(today, in_time)
        out_datetime = datetime.combine(today, out_time)

        # Handle case where OUT is past midnight
        if out_time < in_time:
            out_datetime = datetime.combine(today + timedelta(days=1), out_time)

        session_seconds = time_diff_in_seconds(out_datetime, in_datetime)

        # Get current total (already stored in seconds, default 0)
        current_total = frappe.db.get_value("Overtime Checkin", record_name, "total_overtime") or 0
        current_total = float(current_total)  # ensure it's numeric
        new_total = current_total + session_seconds

        # Optional: format as HH:MM:SS string for readability
        hours = new_total // 3600
        minutes = (new_total % 3600) // 60
        seconds = new_total % 60
        total_str = f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}"

        # Save both seconds (for calculation) and string (for display)
        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)

        frappe.db.sql("""
            UPDATE `tabOvertime Checkin`
            SET total_overtime = %s, modified = %s
            WHERE name = %s
        """, (total_str, current_ist, record_name))

        frappe.db.sql("""
            UPDATE `tabOvertime Sessions`
            SET session_duration = %s, modified = %s
            WHERE parent = %s AND name=%s
        """, (total_str, current_ist, record_name, child_name))

    except Exception as e:
        error_msg = f"Error calculating overtime: {str(e)}"
        frappe.log_error(error_msg, "Overtime Calculation Error")
        raise Exception(error_msg)


def calculate_and_update_security_total(record_name, in_time, out_time):
    """
    Calculate session duration and update total security session time
    """
    # --- helper to normalize values into datetime.time ---
    def _normalize_to_time(val):
        if isinstance(val, time):
            return val  # already correct
        if isinstance(val, str):
            # "HH:MM:SS" → time object
            h, m, *s = map(int, val.split(":"))
            return time(h, m, s[0] if s else 0)
        if isinstance(val, timedelta):
            # timedelta → wrap into 24h clock time
            total_seconds = int(val.total_seconds()) % (24 * 3600)
            h = total_seconds // 3600
            m = (total_seconds % 3600) // 60
            s = total_seconds % 60
            return time(h, m, s)
        raise TypeError(f"Unsupported type for security input: {type(val)}")

    try:
        today = datetime.now().date()

        # normalize inputs before combine()
        in_time = _normalize_to_time(in_time)
        out_time = _normalize_to_time(out_time)

        in_datetime = datetime.combine(today, in_time)
        out_datetime = datetime.combine(today, out_time)

        # Handle case where out_time is next day
        if out_time < in_time:
            out_datetime = datetime.combine(today + timedelta(days=1), out_time)

        session_seconds = time_diff_in_seconds(out_datetime, in_datetime)

        # Add to existing total
        current_total = frappe.db.get_value("Security Gate Checkin", record_name, "total_session_time") or 0
        current_total = float(current_total)  # ensure numeric
        new_total = current_total + session_seconds

        ist = pytz.timezone('Asia/Kolkata')
        current_ist = datetime.now(ist).replace(microsecond=0)

        # Update DB
        frappe.db.sql("""
            UPDATE `tabSecurity Gate Checkin`
            SET total_session_time = %s, modified = %s
            WHERE name = %s
        """, (new_total, current_ist, record_name))

    except Exception as e:
        error_msg = f"Error calculating security time: {str(e)}"
        frappe.log_error(error_msg, "Security Calculation Error")
        raise Exception(error_msg)
