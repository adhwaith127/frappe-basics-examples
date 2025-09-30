import frappe
import json

# adding whitelist decorator exposes this method to api
@frappe.whitelist(allow_guest=True)
def get_designations():
    employee_designations=[] # pluck picks out fieldnames' value and returns those values as list (similar to for) 
    designations=frappe.get_all("Designation",fields=["name"],pluck='name')
    # for designation in designations:
    #     dsg=designation['name']
    #     employee_designations.append(dsg)
    return designations

@frappe.whitelist(allow_guest=True)  # remove allow_guest to remove guest access
def add_employee():
    try:
        data = json.loads(frappe.request.data) 
        name=data.get('employeename')
        designation=data.get('designation')
        if not name or not designation:
            return {"message":"data missing"}
        doc=frappe.new_doc("Employee Form")
        doc.employeename=name
        doc.designation=designation
   
        doc.insert()        
        frappe.db.commit()

        return {"message": "Employee added", "success": True}
    
    except Exception as e:
        frappe.log_error("error",str(e))
        return {"message":"Error in processing"}