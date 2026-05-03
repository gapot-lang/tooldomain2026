# Tool Lọc & Săn Domain

Ứng dụng Streamlit để kiểm tra WHOIS và lịch sử domain.

## Chạy trên máy của bạn

1. Mở `cmd` hoặc PowerShell ở thư mục `Desktop`.
2. Chạy:

```powershell
py -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

3. Mở trình duyệt:

- Local: `http://localhost:8501`
- Mạng nội bộ: `http://192.168.1.48:8501`

> Những người khác trong cùng mạng LAN có thể truy cập `http://192.168.1.48:8501`.

## Chuẩn bị deploy lên Streamlit Cloud

1. Tạo tài khoản GitHub.
2. Tạo repository mới và đẩy các file sau lên:
   - `app.py`
   - `requirements.txt`
   - `README.md`
3. Vào `https://share.streamlit.io`, đăng nhập GitHub.
4. Chọn repo và file `app.py` để deploy.

## Nếu muốn public toàn cầu

Bạn cần một server/VPS có IP công khai hoặc dùng dịch vụ hosting như:
- Streamlit Cloud
- Render
- Railway
- Fly.io

## File đã chuẩn bị

- `requirements.txt`
- `start_streamlit.bat`
- `README.md`
