#!/usr/bin/env python3
import os
import sys
import re
import io
import json
import argparse
import logging
import pandas as pd
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("generation.log", mode='w')
    ]
)

def safe_filename(val):
    """
    Cleans a string to make it safe for filenames.
    Keeps alphanumeric, spaces, hyphens, and underscores.
    """
    cleaned = re.sub(r'[^\w\s-]', '', str(val))
    cleaned = re.sub(r'[\s]+', '_', cleaned)
    return cleaned.strip('_')

def format_field_value(val):
    """
    Formats a field value as a clean string.
    Specifically handles pandas float representations of integers (e.g. 1001.0 -> 1001).
    """
    if pd.isna(val):
        return ""
    try:
        # Check if it has an is_integer method (e.g. float, np.float64)
        if hasattr(val, 'is_integer') and val.is_integer():
            return str(int(val))
        s = str(val).strip()
        if s.endswith('.0'):
            try:
                float_val = float(s)
                if float_val.is_integer():
                    return str(int(float_val))
            except ValueError:
                pass
        return s
    except Exception:
        return str(val).strip()

def load_config(config_path):
    """
    Loads JSON configuration for certificate overlay coordinates and styling.
    """
    if not os.path.exists(config_path):
        logging.error(f"Config file not found: {config_path}")
        sys.exit(1)
    
    with open(config_path, 'r') as f:
        try:
            return json.load(f)
        except Exception as e:
            logging.error(f"Failed to parse config JSON: {e}")
            sys.exit(1)

def register_custom_font(config):
    """
    Registers a custom TTF font if configured.
    """
    custom_font_cfg = config.get("custom_font", {})
    if custom_font_cfg.get("enabled", False):
        font_path = custom_font_cfg.get("path", "")
        font_name = custom_font_cfg.get("name", "CustomFont")
        if font_path and os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                logging.info(f"Successfully registered custom font '{font_name}' from {font_path}")
                return font_name
            except Exception as e:
                logging.error(f"Error registering custom font: {e}")
        else:
            logging.warning(f"Custom font path does not exist: {font_path}. Using system default fonts.")
    return None

def create_overlay_pdf(name, roll_no, dept, config, width, height):
    """
    Generates a PDF overlay page in-memory containing the Name, Roll No, and Department at their coordinates.
    """
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(width, height))
    
    # Register custom font if available
    register_custom_font(config)
    
    # Helper to draw a field
    def draw_field(field_cfg, value):
        if not field_cfg.get("enabled", True):
            return
        
        color = HexColor(field_cfg.get("font_color", "#000000"))
        can.setFillColor(color)
        can.setFont(field_cfg.get("font_name", "Helvetica"), field_cfg.get("font_size", 12))
        
        x = field_cfg.get("x", width / 2.0)
        y = field_cfg.get("y", height / 2.0)
        align = field_cfg.get("align", "center").lower()
        
        if align == "center":
            can.drawCentredString(x, y, str(value))
        elif align == "right":
            can.drawRightString(x, y, str(value))
        else:
            can.drawString(x, y, str(value))

    # Draw Name
    if "name_field" in config:
        draw_field(config["name_field"], name)
        
    # Draw Roll No
    if "roll_field" in config:
        draw_field(config["roll_field"], roll_no)
        
    # Draw Department
    if "dept_field" in config:
        draw_field(config["dept_field"], dept)
        
    can.save()
    packet.seek(0)
    return packet

def create_grid_overlay(width, height):
    """
    Creates an overlay with a grid spaced at 50-point intervals to calibrate X/Y coordinates.
    """
    packet = io.BytesIO()
    can = canvas.Canvas(packet, pagesize=(width, height))
    
    # Red lines every 50 points
    can.setStrokeColor(HexColor("#FF0000"))
    can.setFillColor(HexColor("#FF0000"))
    can.setLineWidth(0.5)
    can.setFont("Helvetica", 8)
    
    # Draw horizontal grid lines
    for y in range(0, int(height), 50):
        can.line(0, y, width, y)
        can.drawString(5, y + 2, str(y))
        can.drawString(width - 25, y + 2, str(y))
        
    # Draw vertical grid lines
    for x in range(0, int(width), 50):
        can.line(x, 0, x, height)
        can.drawString(x + 2, 5, str(x))
        can.drawString(x + 2, height - 12, str(x))
        
    # Highlight major axis (100pt grid) in Blue
    can.setStrokeColor(HexColor("#0000FF"))
    can.setLineWidth(1.0)
    can.setFont("Helvetica-Bold", 9)
    can.setFillColor(HexColor("#0000FF"))
    
    for y in range(0, int(height), 100):
        can.line(0, y, width, y)
    for x in range(0, int(width), 100):
        can.line(x, 0, x, height)
        
    # Draw page boundary labels and size information
    can.setFillColor(HexColor("#008000"))
    can.setFont("Helvetica-Bold", 12)
    can.drawString(20, height - 30, f"Page Size: {width:.1f} x {height:.1f} pt")
    
    can.save()
    packet.seek(0)
    return packet

def get_pdf_dimensions(template_path):
    """
    Reads the dimensions of the first page of the template PDF.
    """
    if not os.path.exists(template_path):
        logging.error(f"Template PDF not found: {template_path}")
        sys.exit(1)
        
    reader = PdfReader(template_path)
    if len(reader.pages) == 0:
        logging.error("Template PDF has no pages.")
        sys.exit(1)
        
    page = reader.pages[0]
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    return width, height, reader

def generate_grid_preview(template_path, output_path):
    """
    Generates a calibration grid overlayed on the template PDF.
    """
    width, height, reader = get_pdf_dimensions(template_path)
    grid_packet = create_grid_overlay(width, height)
    grid_reader = PdfReader(grid_packet)
    grid_page = grid_reader.pages[0]
    
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == 0:
            page.merge_page(grid_page)
        writer.add_page(page)
        
    with open(output_path, "wb") as f:
        writer.write(f)
    logging.info(f"Calibration grid certificate generated at: {output_path}")

def generate_single_certificate(name, roll_no, dept, template_path, output_path, config):
    """
    Generates a single certificate for a given Name, Roll No, and Department.
    """
    width, height, reader = get_pdf_dimensions(template_path)
    overlay_packet = create_overlay_pdf(name, roll_no, dept, config, width, height)
    overlay_reader = PdfReader(overlay_packet)
    overlay_page = overlay_reader.pages[0]
    
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == 0:
            # Clean template page text if configured
            clean_strs = config.get("clean_strings", [])
            if clean_strs and "/Contents" in page:
                try:
                    contents_obj = page["/Contents"].get_object()
                    data = contents_obj.get_data()
                    for s in clean_strs:
                        byte_s = s.encode('utf-8')
                        data = data.replace(b'(' + byte_s + b') Tj', b'() Tj')
                    contents_obj.set_data(data)
                except Exception as e:
                    logging.warning(f"Failed to clean template content stream: {e}")
            
            page.merge_page(overlay_page)
        writer.add_page(page)
        
    with open(output_path, "wb") as f:
        writer.write(f)

def main():
    parser = argparse.ArgumentParser(description="Batch Automated Certificate PDF Generator")
    parser.add_argument("--excel", default="records.xlsx", help="Path to input Excel file")
    parser.add_argument("--template", default="template.pdf", help="Path to background PDF template")
    parser.add_argument("--output", default="output_certificates", help="Output folder path")
    parser.add_argument("--config", default="config.json", help="Path to config JSON settings")
    parser.add_argument("--grid-preview", action="store_true", help="Generate a PDF with a coordinate grid overlay")
    parser.add_argument("--preview", action="store_true", help="Generate only one test certificate from the first row")
    
    args = parser.parse_args()
    
    # 1. Grid Preview workflow
    if args.grid_preview:
        grid_out = "template_grid.pdf"
        logging.info(f"Generating coordinate grid preview on {args.template}...")
        generate_grid_preview(args.template, grid_out)
        print(f"\n--- GRID PREVIEW GENERATED ---")
        print(f"Please open '{grid_out}' in your PDF reader to determine the X and Y coordinates.")
        print(f"Update these coordinates in '{args.config}' before running full generation.\n")
        return

    # Load configuration
    config = load_config(args.config)
    
    # 2. Preview workflow (single test row)
    if args.preview:
        logging.info("Running in PREVIEW mode (generating first row only)...")
        # Load excel to get first row, or use dummy if excel not found
        name_col = config["excel_columns"]["name_column"]
        roll_col = config["excel_columns"]["roll_no_column"]
        dept_col = config["excel_columns"].get("dept_column", "Department")
        
        test_name = "Rahul Kumar"
        test_roll = "10123"
        test_dept = "B.Sc CT"
        
        if os.path.exists(args.excel):
            try:
                df = pd.read_excel(args.excel)
                df.columns = [str(col).strip() for col in df.columns]
                cols_to_check = [name_col, roll_col]
                has_cols = all(c in df.columns for c in cols_to_check)
                if has_cols:
                    dept_present = dept_col in df.columns
                    subset_cols = cols_to_check + [dept_col] if dept_present else cols_to_check
                    valid_rows = df.dropna(subset=subset_cols)
                    if not valid_rows.empty:
                        first_row = valid_rows.iloc[0]
                        test_name = format_field_value(first_row[name_col])
                        test_roll = format_field_value(first_row[roll_col])
                        test_dept = format_field_value(first_row[dept_col]) if dept_present else ""
            except Exception as e:
                logging.warning(f"Could not read excel for preview ({e}). Using default dummy data.")
        
        os.makedirs(args.output, exist_ok=True)
        preview_file = os.path.join(args.output, "preview_certificate.pdf")
        generate_single_certificate(test_name, test_roll, test_dept, args.template, preview_file, config)
        print(f"\n--- PREVIEW GENERATED ---")
        print(f"Successfully generated preview certificate at: '{preview_file}'")
        print(f"Please inspect it to check font, color, alignment, and coordinates.\n")
        return

    # 3. Batch Production workflow
    if not os.path.exists(args.excel):
        logging.error(f"Excel file not found: {args.excel}")
        sys.exit(1)
        
    logging.info(f"Reading records from Excel: {args.excel}")
    try:
        df = pd.read_excel(args.excel)
    except Exception as e:
        logging.error(f"Failed to read Excel file: {e}")
        sys.exit(1)
        
    # Clean column headers
    df.columns = [str(col).strip() for col in df.columns]
    name_col = config["excel_columns"]["name_column"]
    roll_col = config["excel_columns"]["roll_no_column"]
    dept_col = config["excel_columns"].get("dept_column", "Department")
    
    if name_col not in df.columns:
        logging.error(f"Name column '{name_col}' not found. Available columns: {list(df.columns)}")
        sys.exit(1)
    if roll_col not in df.columns:
        logging.error(f"Roll No column '{roll_col}' not found. Available columns: {list(df.columns)}")
        sys.exit(1)
        
    dept_present = dept_col in df.columns
    if not dept_present:
        logging.warning(f"Department column '{dept_col}' not found. Department overlays will be skipped.")
        
    os.makedirs(args.output, exist_ok=True)
    
    total_records = len(df)
    success_count = 0
    failed_records = []
    
    logging.info(f"Starting batch generation of {total_records} certificates...")
    
    # Pre-fetch template dimensions
    width, height, _ = get_pdf_dimensions(args.template)
    
    for idx, row in df.iterrows():
        excel_row_num = idx + 2  # Excel rows are 1-indexed, and row 1 is header
        
        name = row.get(name_col)
        roll = row.get(roll_col)
        dept = row.get(dept_col) if dept_present else ""
        
        # Check for empty/missing values in required columns
        if pd.isna(name) or pd.isna(roll):
            reason = "Missing Name or Roll No"
            failed_records.append({"row": excel_row_num, "name": name, "roll": roll, "reason": reason})
            logging.warning(f"Row {excel_row_num} skipped: {reason}")
            continue
            
        name_str = format_field_value(name)
        roll_str = format_field_value(roll)
        dept_str = format_field_value(dept) if dept_present and not pd.isna(dept) else ""
        
        if not name_str or not roll_str:
            reason = "Empty Name or Roll No string"
            failed_records.append({"row": excel_row_num, "name": name_str, "roll": roll_str, "reason": reason})
            logging.warning(f"Row {excel_row_num} skipped: {reason}")
            continue
            
        # Create safe filename and handle duplicate collisions
        safe_name = safe_filename(name_str)
        safe_roll = safe_filename(roll_str)
        base_filename = f"{safe_roll}_{safe_name}"
        filename = f"{base_filename}.pdf"
        output_path = os.path.join(args.output, filename)
        
        collision_counter = 1
        while os.path.exists(output_path):
            filename = f"{base_filename}_{collision_counter}.pdf"
            output_path = os.path.join(args.output, filename)
            collision_counter += 1
            
        try:
            generate_single_certificate(name_str, roll_str, dept_str, args.template, output_path, config)
            success_count += 1
            if success_count % 100 == 0 or success_count == total_records:
                logging.info(f"Processed {success_count}/{total_records} certificates...")
        except Exception as e:
            reason = f"Generation error: {str(e)}"
            failed_records.append({"row": excel_row_num, "name": name_str, "roll": roll_str, "reason": reason})
            logging.error(f"Row {excel_row_num} ({name_str}, {roll_str}) failed: {e}")
            
    # Print execution summary
    print("\n" + "="*50)
    print("                EXECUTION SUMMARY")
    print("="*50)
    print(f"Total records in Excel:        {total_records}")
    print(f"Certificates generated:        {success_count}")
    print(f"Failed / Skipped records:      {len(failed_records)}")
    print("="*50)
    
    if failed_records:
        print("\nFailed Records Details:")
        print(f"{'Row':<6} | {'Roll No':<15} | {'Name':<25} | {'Reason'}")
        print("-" * 80)
        for fail in failed_records:
            name_val = str(fail['name']) if fail['name'] is not None else "N/A"
            roll_val = str(fail['roll']) if fail['roll'] is not None else "N/A"
            print(f"{fail['row']:<6} | {roll_val:<15} | {name_val:<25} | {fail['reason']}")
        print("="*80 + "\n")
        
    logging.info(f"Batch generation completed. Total generated: {success_count}, Failed: {len(failed_records)}.")

if __name__ == "__main__":
    main()
