# coding: utf-8
import re
import socket
import time
import requests
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st
from datetime import datetime

selenium_available = True
selenium_import_error = ""
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import WebDriverException, TimeoutException
except ModuleNotFoundError as exc:
    selenium_available = False
    selenium_import_error = str(exc)

uc_import_error = ""
try:
    import undetected_chromedriver as uc
except Exception as exc:
    uc = None
    uc_import_error = str(exc)

whois_available = True
whois_import_error = ""
whois_not_found_error = None
try:
    import whois
    from whois.parser import WhoisDomainNotFoundError
    whois_not_found_error = WhoisDomainNotFoundError
except Exception as exc:
    whois_available = False
    whois_import_error = str(exc)


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://", "", value)
    value = value.split("/")[0]
    value = value.strip().strip(".")
    if value.startswith("www."):
        value = value[4:]
    return value


def get_domains_from_input(text: str) -> list[str]:
    domains = [normalize_domain(line) for line in text.splitlines()]
    domains = [domain for domain in domains if domain]
    return list(dict.fromkeys(domains))


def make_history_session() -> requests.Session:
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    scraper.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=frozenset(["HEAD", "GET", "OPTIONS"]),
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    scraper.mount("https://", adapter)
    scraper.mount("http://", adapter)
    return scraper


def domain_resolves(domain: str, timeout: int = 5) -> bool:
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(domain, None)
        return True
    except socket.gaierror:
        return False
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)


def get_first_archive_year(domain: str, status_area, append_log) -> tuple[str | None, bool]:
    session = make_history_session()
    cdx_url = f"http://web.archive.org/cdx/search/cdx?url={domain}&limit=1"

    try:
        response = session.get(cdx_url, timeout=30)
        if response.status_code != 200:
            message = f"[Archive] {domain}: dịch vụ trả về {response.status_code}"
            status_area.text(message)
            append_log(message)
            return None, True

        text = response.text.strip()
        if not text:
            append_log(f"[Archive] Không tìm thấy lịch sử Archive cho {domain}")
            return None, False

        lines = [line for line in text.splitlines() if line.strip()]
        years = set()
        for line in lines:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) >= 4:
                years.add(parts[1][:4])

        if years:
            year_str = ", ".join(sorted(years))
            append_log(f"[Archive] Tìm thấy lịch sử cho {domain}: {year_str}")
            return year_str, False

        append_log(f"[Archive] Không tìm thấy lịch sử Archive cho {domain}")
        return None, False
    except requests.exceptions.Timeout:
        message = f"⚠️ Server quá tải, đã bỏ qua sau 30s chờ cho {domain}"
        status_area.text(message)
        append_log(message)
        return None, True
    except requests.exceptions.ConnectionError as exc:
        message = f"[Archive] {domain}: lỗi kết nối ({exc})"
        status_area.text(message)
        append_log(message)
        return None, True
    except Exception as exc:
        message = f"[Archive] {domain}: lỗi khi truy vấn ({exc})"
        status_area.text(message)
        append_log(message)
        return None, True


def check_crtsh(domain: str, status_area, append_log) -> tuple[bool, bool]:
    session = make_history_session()
    url = f"https://crt.sh/?q={domain}&output=json"

    try:
        response = session.get(url, timeout=45)
        if response.status_code != 200:
            message = f"[crt.sh] {domain}: dịch vụ trả về {response.status_code}"
            status_area.text(message)
            append_log(message)
            return False, True

        data = response.json()
        if isinstance(data, list) and len(data) > 0:
            message = f"[crt.sh] Tìm thấy chứng chỉ cho {domain}"
            status_area.text(message)
            append_log(message)
            return True, False

        append_log(f"[crt.sh] Không có chứng chỉ cho {domain}")
        return False, False
    except requests.exceptions.Timeout:
        message = f"⚠️ Server quá tải, đã bỏ qua sau 45s chờ cho {domain}"
        status_area.text(message)
        append_log(message)
        return False, True
    except requests.exceptions.ConnectionError as exc:
        message = f"[crt.sh] {domain}: lỗi kết nối ({exc})"
        status_area.text(message)
        append_log(message)
        return False, True
    except ValueError as exc:
        message = f"[crt.sh] {domain}: dữ liệu trả về không phải JSON ({exc})"
        status_area.text(message)
        append_log(message)
        return False, True
    except Exception as exc:
        message = f"[crt.sh] {domain}: lỗi {exc}"
        status_area.text(message)
        append_log(message)
        return False, True


def extract_price_from_html(html: str) -> str | None:
    match = re.search(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", html)
    if match:
        return match.group(0).replace(" ", "")
    return None


def check_whois_available(domain: str, status_area, progress_bar, append_log) -> tuple[bool, str | None]:
    result = False
    price = None
    try:
        message = f"[WHOIS] Kiểm tra WHOIS cho {domain}..."
        status_area.text(message)
        append_log(message)
        record = whois.whois(domain)

        domain_name = getattr(record, "domain_name", None)
        registrar = getattr(record, "registrar", None)
        creation_date = getattr(record, "creation_date", None)
        expiration_date = getattr(record, "expiration_date", None)
        status_value = getattr(record, "status", None)
        record_text = (getattr(record, "text", "") or "").lower()

        not_found_markers = [
            "no match",
            "not found",
            "no data found",
            "no entries found",
            "no whois server",
            "status: free",
            "no such domain",
            "available",
        ]

        if domain_name or registrar or creation_date or expiration_date or status_value:
            result = False
            message = f"[WHOIS] Đã bị đăng ký: {domain}"
        elif any(marker in record_text for marker in not_found_markers):
            if domain_resolves(domain):
                result = False
                message = f"[WHOIS] Đã bị đăng ký: {domain} (DNS xác nhận)"
            else:
                result = True
                message = f"[WHOIS] Có thể mua: {domain}"
        else:
            result = False
            message = f"[WHOIS] Đã bị đăng ký: {domain}"

        status_area.text(message)
        append_log(message)
    except Exception as exc:
        error_text = str(exc).lower()

        not_found_markers = [
            "no match",
            "not found",
            "no data found",
            "no entries found",
            "no whois server",
            "status: free",
            "no such domain",
            "available",
        ]

        if whois_not_found_error and isinstance(exc, whois_not_found_error):
            if domain_resolves(domain):
                result = False
                message = f"[WHOIS] Đã bị đăng ký: {domain} (DNS xác nhận)"
            else:
                result = True
                message = f"[WHOIS] Có thể mua: {domain}"
        elif any(marker in error_text for marker in not_found_markers):
            if domain_resolves(domain):
                result = False
                message = f"[WHOIS] Đã bị đăng ký: {domain} (DNS xác nhận)"
            else:
                result = True
                message = f"[WHOIS] Có thể mua: {domain}"
        else:
            result = False
            message = f"[WHOIS] Lỗi WHOIS với {domain}: {exc}"

        status_area.text(message)
        append_log(message)
    return result, price


def create_browser() -> webdriver.Chrome:
    def make_options() -> webdriver.ChromeOptions:
        opts = uc.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--lang=en-US")
        return opts

    if uc:
        options = make_options()
        try:
            return uc.Chrome(options=options)
        except Exception as exc:
            message = str(exc)
            version_match = re.search(r"Current browser version is (\d+)", message)
            if version_match:
                version_main = int(version_match.group(1))
                try:
                    return uc.Chrome(version_main=version_main, options=make_options())
                except Exception as exc2:
                    raise RuntimeError(
                        f"Cannot start undetected-chromedriver for Chrome version {version_main}: {exc2}. Original error: {message}"
                    )
            raise RuntimeError(f"Cannot start undetected-chromedriver: {message}")

    advice = "python -m pip install selenium undetected-chromedriver"
    if uc_import_error:
        raise RuntimeError(
            f"Cannot import undetected-chromedriver: {uc_import_error}. Install or fix the package with: {advice}"
        )

    raise RuntimeError(
        f"Missing undetected-chromedriver. Install with: {advice}"
    )


def main() -> None:
    st.set_page_config(page_title="Tool Lọc & Săn Domain", layout="wide", page_icon="Web")
    st.markdown(
        "<style>"
        "body {background-color: #eaf4ff; color: #0b2f5f;}"
        ".stApp {background-color: #f4faff; color: #0b2f5f;}"
        ".stButton>button {background-color: #0f4c81; color: white; border: none; box-shadow: none;}"
        ".stButton>button:hover {background-color: #0b3970; color: white;}"
        ".stTextArea>div>div>textarea {border-color: #0f4c81; background: #ffffff; color: #0b2f5f;}"
        ".stTextArea>label {color: #0b3c78;}"
        "</style>"
        "<div style='padding: 1rem; border-radius: 16px; margin-bottom: 1rem; background: linear-gradient(135deg, #d7e8ff 0%, #b3d4ff 100%);'>"
        "<h1 style='color:#0b3c78; margin:0;'>Tool Lọc & Săn Domain</h1>"
        "<p style='color:#0f4c81; margin:0.3rem 0 0;'>Kiểm tra WHOIS trước, rồi xác định lịch sử lưu trữ domain.</p>"
        "</div>"
        , unsafe_allow_html=True,
    )

    if not whois_available:
        st.error(
            "Ứng dụng này yêu cầu python-whois. Cài đặt bằng: python -m pip install python-whois"
        )

    if "domain_input" not in st.session_state:
        st.session_state.domain_input = ""

    if st.button("Sử dụng domain test khả dụng"):
        st.session_state.domain_input = "mycooldomain2026abcxyz.com\nthudomaintest12345.com"

    domain_input = st.text_area(
        "Danh sách domain",
        placeholder="example.com\nexample.net\nexample.org",
        height=320,
        key="domain_input",
    )

    st.caption("Nhấn nút 'Sử dụng domain test khả dụng' để điền mẫu domain. Thông tin chi tiết sẽ xuất hiện trong phần Log.")
    start_button = st.button("Bắt đầu lọc domain")
    status_area = st.empty()
    progress_bar = st.progress(0)
    log_expander = st.expander("Log chi tiết", expanded=False)
    log_text = log_expander.empty()
    log_messages: list[str] = []

    def append_log(message: str) -> None:
        log_messages.append(message)
        status_area.text(message)
        log_text.text("\n".join(log_messages))

    if start_button:
        domains = get_domains_from_input(domain_input)
        if not domains:
            st.warning("Vui lòng dán ít nhất một domain vào danh sách.")
            progress_bar.progress(0)
            return

        try:
            progress_bar.progress(5)
            append_log("Bắt đầu kiểm tra WHOIS...")

            whois_results = []
            available_domains = []
            total = len(domains)

            for index, domain in enumerate(domains, start=1):
                progress_bar.progress(int(5 + (index / total) * 40))
                append_log(f"[WHOIS] Đang kiểm tra {index}/{total}: {domain}")

                whois_ok, price = check_whois_available(domain, status_area, progress_bar, append_log)
                if whois_ok:
                    available_domains.append(domain)
                    whois_results.append({
                        "Domain": domain,
                        "WHOIS": "Có thể mua",
                        "Giá": price or "Không xác định",
                    })
                else:
                    whois_results.append({
                        "Domain": domain,
                        "WHOIS": "Không thể mua",
                        "Giá": price or "Không có",
                    })

            progress_bar.progress(50)
            append_log("Hoàn tất kiểm tra WHOIS.")

            left_col, right_col = st.columns(2)
            left_placeholder = left_col.empty()
            right_placeholder = right_col.empty()

            with left_col:
                st.subheader("Danh sách domain có thể mua")
                if available_domains:
                    left_placeholder.dataframe({
                        "Domain": available_domains,
                    })
                else:
                    left_placeholder.info("Không có domain nào có thể mua được.")

            with right_col:
                st.subheader("Kết quả lịch sử")
                right_placeholder.info("Đang chờ hoàn thành WHOIS để kiểm tra lịch sử...")

            archive_results = []
            if available_domains:
                append_log("Bắt đầu kiểm tra lịch sử cho các domain có thể mua...")
                for idx, domain in enumerate(available_domains, start=1):
                    progress_bar.progress(int(50 + (idx / len(available_domains)) * 45))
                    append_log(f"[Archive] Đang kiểm tra lịch sử {idx}/{len(available_domains)}: {domain}")

                    year, archive_failure = get_first_archive_year(domain, status_area, append_log)
                    if year:
                        archive_value = f"Năm đầu: {year}"
                    else:
                        append_log(f"[crt.sh] Kiểm tra dự phòng cho {domain}...")
                        has_crt, crt_failure = check_crtsh(domain, status_area, append_log)
                        if has_crt:
                            archive_value = "crt.sh tìm thấy"
                        elif archive_failure and crt_failure:
                            archive_value = "Không xác định do cả Archive và crt.sh tạm thời lỗi"
                        elif crt_failure:
                            archive_value = "Không xác định do crt.sh tạm thời lỗi"
                        elif archive_failure:
                            archive_value = "Không xác định do dịch vụ Archive tạm thời lỗi"
                        else:
                            archive_value = "Không có lịch sử"

                    archive_results.append({
                        "Domain": domain,
                        "Lịch sử / crt.sh": archive_value,
                    })

                if archive_results:
                    right_placeholder.dataframe({
                        "Domain": [r["Domain"] for r in archive_results],
                        "Lịch sử / crt.sh": [r["Lịch sử / crt.sh"] for r in archive_results],
                    })
                else:
                    right_placeholder.info("Không có dữ liệu lịch sử để hiển thị.")
            else:
                append_log("Không có domain nào đủ điều kiện để kiểm tra lịch sử.")
                right_placeholder.info("Không có domain nào được kiểm tra lịch sử vì không có domain khả dụng.")

            progress_bar.progress(100)
            append_log("Hoàn tất!")

            if available_domains:
                st.success(f"Đã kiểm tra xong. {len(available_domains)} domain có thể mua và đã kiểm tra lịch sử.")
            else:
                st.warning("Không có domain nào có thể mua, nên không kiểm tra lịch sử.")

        except RuntimeError as exc:
            progress_bar.progress(0)
            st.error(f"Lỗi: {exc}")
        except Exception as exc:
            progress_bar.progress(0)
            st.error(f"Lỗi không mong đợi: {exc}")


if __name__ == "__main__":
    main()
