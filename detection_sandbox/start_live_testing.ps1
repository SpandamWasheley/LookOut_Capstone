# Start Live Smoking Detection Testing Web App
# Sets up environment and launches the Flask web interface

Write-Host "========================================"
Write-Host "LookOut Live Smoking Detection Testing"
Write-Host "========================================"
Write-Host ""

# Set environment variables
$env:SMOKING_MODEL = "C:\Users\User\OneDrive\Desktop\CAPSTONE\runs\detect\smoking_v5_crops\weights\best.pt"
$env:KMP_DUPLICATE_LIB_OK = "TRUE"

Write-Host "Model: $env:SMOKING_MODEL"
Write-Host "OpenMP Fix: $env:KMP_DUPLICATE_LIB_OK"
Write-Host ""

# Check if model exists
if (Test-Path $env:SMOKING_MODEL) {
    Write-Host "✓ Model file found" -ForegroundColor Green
} else {
    Write-Host "✗ Model file NOT found!" -ForegroundColor Red
    Write-Host "Please check the SMOKING_MODEL path in this script."
    exit 1
}

Write-Host ""
Write-Host "Starting Flask web app..."
Write-Host "Open your browser to: http://localhost:5003"
Write-Host ""
Write-Host "Features:"
Write-Host "  • Start Live: Real-time detection without recording"
Write-Host "  • Record Live: Real-time detection WITH video recording"
Write-Host "  • Adjustable confidence threshold"
Write-Host "  • Live frame display and statistics"
Write-Host ""

# Change to detection_sandbox directory
Set-Location (Split-Path $MyInvocation.MyCommand.Path)

# Run the Flask app
python live_testing.py
