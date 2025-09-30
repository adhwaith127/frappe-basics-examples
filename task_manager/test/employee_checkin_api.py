import frappe
from frappe.utils.data import format_datetime
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime
from datetime import datetime
import json
import traceback


from hrms.hr.doctype.shift_assignment.shift_assignment import (
	get_actual_start_end_datetime_of_shift,
)
from hrms.hr.utils import validate_active_employee

#for changing the date&time format
def convert_datetime(datetime_str):
    try:
        # Convert datetime string to datetime object
        datetime_obj = frappe.utils.data.get_datetime(datetime_str)

        # Format datetime object to the desired format
        formatted_datetime = format_datetime(datetime_obj, "dd-MM-yyyy HH:mm:ss")

        return formatted_datetime
    except Exception as e:
        frappe.logger().error(f"Error converting datetime: {e}")
        return datetime_str


@frappe.whitelist(allow_guest=True)
def getAllEmployee():
    return frappe.db.sql("""Select * from `tabEmployee`;""",as_dict=True)


@frappe.whitelist(allow_guest=True)
def getAllShiftType():
    return frappe.db.sql("""Select * from `tabShift Type`;""",as_dict=True)    


@frappe.whitelist(allow_guest=True)
def getAllShiftTypeWithData(data):
    try:
        return frappe.db.sql("""Select s.custom_duration_for_face_detection_interval from `tabShift Type` as s left join employee as e on s.name=e.default_shift;""",as_dict=True)    
    except Exception as e:
        return {}    



#allow_guest=True ,used to allow any user to access the data using the api call
@frappe.whitelist(allow_guest=True)
def AddCheckInStatus(data):
    try:
        # Parse JSON data
        data_dict = frappe.parse_json(data)
        
        # Extract relevant data
        emp_code = data_dict.get("enrollid")
        date_time_str = data_dict.get("time")
        event = data_dict.get("event")
        name = data_dict.get("name")
        mode = data_dict.get("mode")
        inout = data_dict.get("inout")
        skip_auto_attendance = 0  # default entry is zero or unchecking the selection.

        if not name or not date_time_str:
            frappe.throw(_("'name' and 'time' are required."))

        # Log extracted data
        frappe.logger().info(f"Extracted data: {data_dict}")

        # Get employee details
        employee = frappe.db.get_value("Employee", {"employee_name": name}, ["name", "employee_name"], as_dict=True)
        if not employee:
            frappe.throw(_("No Employee found for the given name: {}".format(name)))
        
        # print(f"Employee checkin name:{name}")
        # print(f"Employee checkin date_time_str:{date_time_str}")

        resp= minLoginTimeCalc(name,date_time_str)
        
        # print(f"Employee checkin response:{str(resp)}")
        # return {"success": False, "message": "Error adding Employee Check-in",
        # "Resp":f"{str(resp)}"}

        for entry in resp:
            name = entry.get('name')
            time_interval = entry.get('time_interval')
            last_punch_time = entry.get('lastPunchTime')
            date_change = entry.get('datechange')
            time_change = entry.get('timechange')
            log_type = entry.get('log_type')
            last_entry_date = entry.get('last_entry_date')

            if date_change == 0:
                if time_change > time_interval:
                    return handle_same_day_checkin(log_type, name, date_time_str)
                else:
                    return {
                    "success": False,
                    "message": "Error adding Employee Check-in. Please try after some time."
                    }   
            else:
                return handle_different_day_checkin(log_type, name, date_time_str,last_entry_date)

            

        # # Get the last check-in details to check for duplicates
        # last_checkin_details = get_last_checkin_details(employee.employee_name)
        
        # if last_checkin_details:
        #     # Compare dates (without time) to check for same-day entries
        #     last_checkin_date = last_checkin_details.time.date()
        #     current_checkin_date = datetime.strptime(date_time_str, "%Y-%m-%d %H:%M:%S").date()
        #     resp= minLoginTimeCalc(name,current_checkin_date)
        #     print(f"response:{str(resp)}")
        #     if last_checkin_date == current_checkin_date:
        #         return handle_same_day_checkin(last_checkin_details, name, date_time_str)
        #     else:
        #         return handle_different_day_checkin(last_checkin_details, name, date_time_str)
        # else:
        #     return create_checkin("IN", name, date_time_str)
            
    except Exception as e:
        frappe.log_error(f"Error adding Employee Check-in: {str(e)}", "AddCheckInStatus")
        return {"success": False, "message": f"Error adding Employee Check-in: {str(e)}"}

def get_last_checkin_details(employee_name):
    # Fetch the last check-in details for the employee
    return frappe.db.get_value("Employee Checkin", {"employee": employee_name}, "*", order_by="time DESC", as_dict=True)



def handle_same_day_checkin(log_type, name, date_time_str):
    if log_type == "IN":
        return create_checkin("OUT", name, date_time_str)
    elif log_type == "OUT":
        return create_checkin("IN", name, date_time_str)


def handle_different_day_checkin(log_type, name, date_time_str,last_entry_date):
    if log_type == "IN":
        create_checkin("OUT", name, last_entry_date)
    return create_checkin("IN", name, date_time_str)


def create_checkin(log_type, name, date_time_str):
    # Create and insert Employee Checkin
    employee_checkin = {
        "doctype": "Employee Checkin",
        "employee": name,
        "log_type": log_type,
        "time": date_time_str
    }
    frappe.get_doc(employee_checkin).insert(ignore_permissions=True)

    # Create and insert Employee Checkin Log
    employee_checkin_log = {
        "doctype": "Employee Checkin log",
        "employee": name,
        "log_type": log_type,
        "time": date_time_str,
        "is_valid": "Valid"
    }

    try:
        frappe.get_doc(employee_checkin_log).insert(ignore_permissions=True)
    except Exception as e:
        # Log error for Employee Checkin Log insertion failure
        frappe.logger().error(f"Error inserting Employee Checkin Log: {str(e)}")
        frappe.logger().error(traceback.format_exc())
        return {
            "success": False,
            "message": f"Error inserting Employee Checkin Log: {str(e)}",
            "traceback": traceback.format_exc()
        }    

    # Commit the transaction
    frappe.db.commit()

    return {
        "success": True,
        "message": "Employee Check-in added successfully",
        "EmpName": name,
        "Status": log_type
    }


def get_last_checkin_details(employee_name):
    # Query the Employee Checkin document to get the last check-in details
    checkin_details = frappe.db.get_all(
        "Employee Checkin",
        filters={"employee": employee_name},
        fields=["name", "time", "log_type"],
        order_by="creation DESC",
        limit=1
    )

    if checkin_details:
        # print("Previous checkin details......")
        # print(checkin_details[0])
        return checkin_details[0]
    else:
        return None



@frappe.whitelist(allow_guest=True)
def getAllEmployeeDetails():
    # Clear the cache
    frappe.clear_cache()

    # for returning all the customer details which are not updated in the tally application.
    return frappe.db.sql("""Select * from `tabEmployee`;""",as_dict=True)



# def minLoginTimeCalc(name,date_time_str):
#     return frappe.db.sql("""SELECT TE.name,
#         TS.custom_attendance_capture_acceptance_interval as time_interval,
#         IFNULL(TA.time,'') AS lastPunchTime,IFNULL(DATEDIFF(%s,
#         TA.time),0) AS datechange,IFNULL(TIMESTAMPDIFF(MINUTE,TA.time,
#         %s),TS.custom_attendance_capture_acceptance_interval+1)
#         AS timechange,IFNULL(log_type,'OUT') AS log_type,IFNULL(TA.time,'') as last_entry_date FROM tabEmployee TE LEFT OUTER JOIN 
#         `tabEmployee Checkin` TA ON TA.employee_name=TE.name LEFT OUTER JOIN 
#         `tabShift Type` TS ON TE.default_shift=TS.name WHERE TE.employee_name=%s
#         ORDER BY TA.time DESC LIMIT 1;""",
#         (date_time_str,date_time_str,name,),as_dict=True)  
    # return frappe.db.sql("""SELECT TE.name,
    # TS.custom_attendance_capture_acceptance_interval as time_interval,
    # IFNULL(TA.time,'') AS lastPunchTime,IFNULL(DATEDIFF(%s,
    # TA.time),0) AS datechange,IFNULL(TIMESTAMPDIFF(MINUTE,TA.time,
    # %s),TS.custom_attendance_capture_acceptance_interval+1)
    #  AS timechange,IFNULL(log_type,'OUT') FROM tabEmployee TE LEFT OUTER JOIN 
    #  `tabEmployee Checkin` TA ON TA.employee_name=TE.name LEFT OUTER JOIN 
    #  `tabShift Type` TS ON TE.default_shift=TS.name WHERE TE.employee_name=%s
    # ORDER BY TA.time DESC LIMIT 1;""",
    # (date_time_str,date_time_str,name,),as_dict=True)  


def minLoginTimeCalc(name, date_time_str):
    return frappe.db.sql("""
        SELECT TE.name,
               TS.custom_attendance_capture_acceptance_interval AS time_interval,
               IFNULL(TA.time, '') AS lastPunchTime,
               IFNULL(DATEDIFF(%s, TA.time), 0) AS datechange,
               IFNULL(TIMESTAMPDIFF(MINUTE, TA.time, %s), TS.custom_attendance_capture_acceptance_interval + 1) AS timechange,
               IFNULL(log_type, 'OUT') AS log_type,
               IFNULL(TA.time, '') AS last_entry_date
        FROM `tabEmployee` TE
        LEFT JOIN `tabEmployee Checkin` TA ON TA.employee_name = TE.name
        LEFT JOIN `tabShift Type` TS ON TE.default_shift = TS.name
        WHERE TE.employee_name = %s
        ORDER BY TA.time DESC
        LIMIT 1
    """, (date_time_str, date_time_str, name), as_dict=True)


#convert to pdf and send through mail.
def convert_and_send_excel_as_pdf(file_path, recipient_email, subject, message):
    try:
        # Ensure the .xlsx file exists
        if not os.path.exists(file_path):
            frappe.throw(f"The file {file_path} does not exist.")

        # Convert .xlsx to .pdf
        pdf_file_path = file_path.replace(".xlsx", ".pdf")
        converter = Xlsx2Pdf(file_path, pdf_file_path)
        converter.convert()

        # Save the .pdf file in Frappe's File system
        with open(pdf_file_path, "rb") as pdf_file:
            pdf_content = pdf_file.read()
            pdf_file_doc = save_file(
                os.path.basename(pdf_file_path),
                pdf_content,
                doctype="File",
                is_private=1
            )

        # Send email with the PDF attachment
        attachments = [{
            "fname": pdf_file_doc.file_name,
            "fcontent": pdf_content
        }]

        frappe.sendmail(
            recipients=[recipient_email],
            subject=subject,
            message=message,
            attachments=attachments
        )

        frappe.msgprint(f"PDF sent successfully to {recipient_email}")

        # Clean up temporary files
        os.remove(file_path)
        os.remove(pdf_file_path)

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error in Convert and Send Excel as PDF")
        frappe.throw(f"An error occurred: {str(e)}")
