import os
import uuid
import json
import shutil
import threading
import zipfile
import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template

# Import existing helpers from generate_certificates
from generate_certificates import (
    generate_single_certificate,
    format_field_value,
    safe_filename,
    get_pdf_dimensions
)

app = Flask(__name__, template_folder='templates', static_folder='static')

# Base directories (inside the workspace)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMP_DIR = os.path.join(BASE_DIR, 'temp_uploads')
os.makedirs(TEMP_DIR, exist_ok=True)

# In-memory store for background tasks
tasks = {}

def clean_old_tasks():
    """Simple helper to remove task folders if there are too many (keeps last 20)"""
    try:
        subdirs = [os.path.join(TEMP_DIR, d) for d in os.listdir(TEMP_DIR) if os.path.isdir(os.path.join(TEMP_DIR, d))]
        if len(subdirs) > 20:
            # Sort by modification time
            subdirs.sort(key=os.path.getmtime)
            for old_dir in subdirs[:-15]:
                shutil.rmtree(old_dir, ignore_errors=True)
    except Exception as e:
        print(f"Error cleaning old tasks: {e}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/upload-excel', methods=['POST'])
def upload_excel():
    """Parses an Excel file and returns its column names"""
    if 'excel' not in request.files:
        return jsonify({"error": "No excel file provided"}), 400
        
    excel_file = request.files['excel']
    if excel_file.filename == '':
        return jsonify({"error": "No file selected"}), 400
        
    task_id = uuid.uuid4().hex
    task_folder = os.path.join(TEMP_DIR, task_id)
    os.makedirs(task_folder, exist_ok=True)
    
    excel_path = os.path.join(task_folder, 'uploaded_records.xlsx')
    excel_file.save(excel_path)
    
    try:
        df = pd.read_excel(excel_path)
        # Clean headers
        columns = [str(col).strip() for col in df.columns]
        
        # Suggest default mappings based on simple keywords
        suggested = {
            "name": "",
            "roll": "",
            "dept": ""
        }
        for col in columns:
            col_lower = col.lower()
            if 'name' in col_lower:
                suggested['name'] = col
            elif 'roll' in col_lower or 'reg' in col_lower or 'id' in col_lower:
                suggested['roll'] = col
            elif 'dept' in col_lower or 'department' in col_lower or 'branch' in col_lower:
                suggested['dept'] = col
                
        return jsonify({
            "success": True,
            "temp_excel_path": excel_path,
            "columns": columns,
            "suggested_mapping": suggested
        })
    except Exception as e:
        shutil.rmtree(task_folder, ignore_errors=True)
        return jsonify({"error": f"Failed to parse Excel file: {str(e)}"}), 400

@app.route('/api/preview', methods=['POST'])
def preview_certificate():
    """Generates a single preview certificate page"""
    template_file = request.files.get('template')
    excel_file = request.files.get('excel')
    
    # Coordinates and config from client
    config_str = request.form.get('config')
    if not config_str:
        return jsonify({"error": "Missing coordinates configuration"}), 400
        
    config = json.loads(config_str)
    
    task_id = uuid.uuid4().hex
    task_folder = os.path.join(TEMP_DIR, task_id)
    os.makedirs(task_folder, exist_ok=True)
    
    # Save template
    if not template_file or template_file.filename == '':
        return jsonify({"error": "No template certificate (PDF) provided"}), 400
    
    template_path = os.path.join(task_folder, 'template.pdf')
    template_file.save(template_path)
    
    # Dummy or actual first record
    name_val = "Rahul Kumar"
    roll_val = "22BCT001"
    dept_val = "B.Sc Computer Technology"
    
    if excel_file and excel_file.filename != '':
        excel_path = os.path.join(task_folder, 'records.xlsx')
        excel_file.save(excel_path)
        try:
            df = pd.read_excel(excel_path)
            df.columns = [str(col).strip() for col in df.columns]
            
            name_col = config.get("excel_columns", {}).get("name_column")
            roll_col = config.get("excel_columns", {}).get("roll_no_column")
            dept_col = config.get("excel_columns", {}).get("dept_column")
            
            if name_col in df.columns:
                name_val = format_field_value(df.iloc[0][name_col])
            if roll_col in df.columns:
                roll_val = format_field_value(df.iloc[0][roll_col])
            if dept_col in df.columns:
                dept_val = format_field_value(df.iloc[0][dept_col])
        except Exception as e:
            # Fallback to dummy data
            pass
            
    preview_output_path = os.path.join(task_folder, 'preview.pdf')
    
    try:
        generate_single_certificate(name_val, roll_val, dept_val, template_path, preview_output_path, config)
        
        # Clean up files after sending
        @request.after_this_request
        def cleanup(response):
            try:
                shutil.rmtree(task_folder, ignore_errors=True)
            except Exception:
                pass
            return response
            
        return send_file(preview_output_path, mimetype='application/pdf')
    except Exception as e:
        shutil.rmtree(task_folder, ignore_errors=True)
        return jsonify({"error": f"Failed to generate preview: {str(e)}"}), 500

def run_batch_generation(task_id, template_path, excel_path, config):
    """Target function for background thread batch certificate generation"""
    task_folder = os.path.dirname(template_path)
    output_folder = os.path.join(task_folder, 'output')
    os.makedirs(output_folder, exist_ok=True)
    
    try:
        df = pd.read_excel(excel_path)
        df.columns = [str(col).strip() for col in df.columns]
        
        name_col = config["excel_columns"]["name_column"]
        roll_col = config["excel_columns"]["roll_no_column"]
        dept_col = config["excel_columns"].get("dept_column", "Department")
        
        dept_present = dept_col in df.columns
        total_records = len(df)
        
        tasks[task_id]["total"] = total_records
        
        width, height, _ = get_pdf_dimensions(template_path)
        
        success_count = 0
        for idx, row in df.iterrows():
            if tasks[task_id]["status"] == "cancelled":
                break
                
            name = row.get(name_col)
            roll = row.get(roll_col)
            dept = row.get(dept_col) if dept_present else ""
            
            # Format and validate
            if pd.isna(name) or pd.isna(roll):
                continue
                
            name_str = format_field_value(name)
            roll_str = format_field_value(roll)
            dept_str = format_field_value(dept) if dept_present and not pd.isna(dept) else ""
            
            if not name_str or not roll_str:
                continue
                
            # Create unique file name
            safe_name = safe_filename(name_str)
            safe_roll = safe_filename(roll_str)
            base_filename = f"{safe_roll}_{safe_name}"
            filename = f"{base_filename}.pdf"
            output_path = os.path.join(output_folder, filename)
            
            collision_counter = 1
            while os.path.exists(output_path):
                filename = f"{base_filename}_{collision_counter}.pdf"
                output_path = os.path.join(output_folder, filename)
                collision_counter += 1
                
            generate_single_certificate(name_str, roll_str, dept_str, template_path, output_path, config)
            success_count += 1
            tasks[task_id]["completed"] = success_count
            
        if tasks[task_id]["status"] != "cancelled":
            # Create a zip of all outputs
            zip_path = os.path.join(task_folder, 'certificates.zip')
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for root, dirs, files in os.walk(output_folder):
                    for file in files:
                        file_path = os.path.join(root, file)
                        zip_file.write(file_path, os.path.relpath(file_path, output_folder))
                        
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["zip_path"] = zip_path
            
    except Exception as e:
        tasks[task_id]["status"] = "failed"
        tasks[task_id]["error"] = str(e)
        
@app.route('/api/generate', methods=['POST'])
def generate_certificates_api():
    """Launches the certificate generation task in a background thread"""
    template_file = request.files.get('template')
    excel_file = request.files.get('excel')
    
    config_str = request.form.get('config')
    if not config_str:
        return jsonify({"error": "Missing coordinates configuration"}), 400
        
    config = json.loads(config_str)
    
    if not template_file or template_file.filename == '':
        return jsonify({"error": "No template certificate (PDF) provided"}), 400
    if not excel_file or excel_file.filename == '':
        return jsonify({"error": "No excel records file provided"}), 400
        
    task_id = uuid.uuid4().hex
    task_folder = os.path.join(TEMP_DIR, task_id)
    os.makedirs(task_folder, exist_ok=True)
    
    template_path = os.path.join(task_folder, 'template.pdf')
    excel_path = os.path.join(task_folder, 'records.xlsx')
    
    template_file.save(template_path)
    excel_file.save(excel_path)
    
    # Initialize task status
    tasks[task_id] = {
        "status": "processing",
        "completed": 0,
        "total": 0,
        "zip_path": None,
        "error": None
    }
    
    # Run cleanup of old tasks asynchronously
    clean_old_tasks()
    
    # Start thread
    thread = threading.Thread(target=run_batch_generation, args=(task_id, template_path, excel_path, config))
    thread.start()
    
    return jsonify({
        "success": True,
        "task_id": task_id
    })

@app.route('/api/status/<task_id>', methods=['GET'])
def get_status(task_id):
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
        
    return jsonify(tasks[task_id])

@app.route('/api/download/<task_id>', methods=['GET'])
def download_certificates(task_id):
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
        
    task = tasks[task_id]
    if task["status"] != "completed" or not task["zip_path"] or not os.path.exists(task["zip_path"]):
        return jsonify({"error": "File not ready or failed"}), 400
        
    return send_file(task["zip_path"], as_attachment=True, download_name="certificates.zip")

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
