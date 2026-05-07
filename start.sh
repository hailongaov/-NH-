#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 Khởi động IPA Scanner..."
echo ""

# Check python3
if ! command -v python3 &>/dev/null; then
    echo "❌ Cần cài Python 3"
    exit 1
fi

# Install deps if needed
python3 -c "import flask" 2>/dev/null || pip3 install flask flask-cors cryptography --break-system-packages

# Get WSL IP
IP=$(hostname -I | awk '{print $1}')
echo "✅ Server chạy tại: http://$IP:5000"
echo "   Mở trình duyệt và truy cập địa chỉ trên"
echo ""

python3 app.py
