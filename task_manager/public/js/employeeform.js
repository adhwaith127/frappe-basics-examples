let csrfToken = null;

document.addEventListener('DOMContentLoaded', function() {
    initializeApp();
});

async function initializeApp() {
    await getCSRFToken();
    await loadDesignations();
    setupFormSubmission();
}

// ================= CSRF Token Management =================
async function getCSRFToken() {
    try {
        if (typeof csrf_token !== 'undefined' && csrf_token) {
            csrfToken = csrf_token; return;
        }
        if (typeof frappe !== 'undefined' && frappe.csrf_token) {
            csrfToken = frappe.csrf_token; return;
        }
        const metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag && metaTag.getAttribute('content')) {
            csrfToken = metaTag.getAttribute('content'); return;
        }
        const frappeMetaTag = document.querySelector('meta[name="X-Frappe-CSRF-Token"]');
        if (frappeMetaTag && frappeMetaTag.getAttribute('content')) {
            csrfToken = frappeMetaTag.getAttribute('content'); return;
        }
        const cookies = document.cookie.split(';');
        for (let cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'csrf_token' || name === 'csrftoken') {
                csrfToken = decodeURIComponent(value); return;
            }
        }
        // API fetch fallback
        const response = await fetch('/api/method/frappe.sessions.get_csrf_token');
        if (response.ok) {
            const data = await response.json();
            if (data.message) csrfToken = data.message;
        }
    } catch (error) {
        console.error('CSRF token fetch error:', error);
        csrfToken = null;
    }
}

// ================= Load Designations =================
async function loadDesignations() {
    try {
        showMessage('Loading designations...', 'loading');
        const response = await fetch('/api/method/task_manager.services.employeeform.get_designations');
        const data = await response.json();
        populateDesignationDropdown(data);
        clearMessage();
    } catch (error) {
        console.error('Error loading designations:', error);
        showMessage('Error loading designations. Please refresh the page.', 'error');
    }
}

function populateDesignationDropdown(data) {
    const selectElement = document.getElementById('designation');
    selectElement.innerHTML = '<option value="">Select Designation</option>';
    const designations = data.message || data;
    if (Array.isArray(designations)) {
        designations.forEach(d => {
            const option = document.createElement('option');
            option.value = d.name || d;
            option.textContent = d.title || d.name || d;
            selectElement.appendChild(option);
        });
    } else {
        showMessage('Invalid data format received for designations', 'error');
    }
}

// ================= UI Helper =================
function showMessage(message, type) {
    const messageArea = document.getElementById('messageArea');
    messageArea.innerHTML = `<div class="message ${type}">${message}</div>`;
}

function clearMessage() {
    const messageArea = document.getElementById('messageArea');
    messageArea.innerHTML = '';
}

// ================= Form Submission =================
function setupFormSubmission() {
    document.getElementById('employeeForm').addEventListener('submit', async function(event) {
        event.preventDefault();
        await submitEmployeeForm();
    });
}

async function submitEmployeeForm() {
    const submitBtn = document.getElementById('submitBtn');
    const originalText = submitBtn.textContent;
    try {
        const employeename = document.getElementById('employeename').value.trim();
        const designation = document.getElementById('designation').value.trim();
        if (!employeename || !designation) { showMessage('Please fill in all required fields', 'error'); return; }

        submitBtn.disabled = true;
        submitBtn.textContent = 'Submitting...';
        showMessage('Adding employee...', 'loading');

        const formData = { employeename, designation };
        const headers = { 'Content-Type': 'application/json' };
        headers['X-Frappe-CSRF-Token'] = frappe.csrf_token;

        const response = await fetch('/api/method/task_manager.services.employeeform.add_employee', {
            method: 'POST',
            headers,
            body: JSON.stringify(formData)
        });

        const result = await response.json();
        if (result.message && result.message.success) {
            showMessage('Employee added successfully!', 'success');
            document.getElementById('employeeForm').reset();
        } else if (result.message) {
            const errorMsg = typeof result.message === 'string' ? result.message : 'Unknown error';
            showMessage(`Error: ${errorMsg}`, 'error');
        } else {
            showMessage('Unexpected response format', 'error');
        }
    } catch (error) {
        console.error('Error submitting form:', error);
        showMessage('Error adding employee. Please try again.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = originalText;
    }
}

// ================= Auto-hide messages =================
setInterval(() => {
    const messageArea = document.getElementById('messageArea');
    const messages = messageArea.querySelectorAll('.success, .loading');
    messages.forEach(msg => setTimeout(() => { if(msg.parentElement) msg.remove(); }, 3000));
}, 100);