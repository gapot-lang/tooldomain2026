# coding: utf-8
import re
import socket
import requests
import cloudscraper
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import streamlit as st
from datetime import datetime
import xml.etree.ElementTree as ET


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


def strip_namespace(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def find_elements_by_local_name(root: ET.Element, local_name: str) -> list[ET.Element]:
    return [element for element in root.iter() if strip_namespace(element.tag) == local_name]


def extract_tld_error_domains(error_text: str) -> list[str]:
    return re.findall(r"Tld for ['\"]([^'\"]+)['\"] is not found", error_text)


def get_secret_value(key: str, default: str = "") -> str:
    try:
        return st.secrets["namecheap"].get(key, default)
    except Exception:
        return default


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


def check_namecheap_api(domains: list[str], api_user: str, api_key: str, user_name: str, client_ip: str) -> list[str]:
    if not domains:
        return []

    endpoint = "https://api.namecheap.com/xml.response"
    available_domains: list[str] = []
    username = user_name or api_user

    def check_batch(batch: list[str]) -> None:
        nonlocal available_domains
        if not batch:
            return

        domain_list = ",".join(batch)
        params = {
            "ApiUser": api_user,
            "ApiKey": api_key,
            "UserName": username,
            "Command": "namecheap.domains.check",
            "ClientIp": client_ip,
            "DomainList": domain_list,
        }

        try:
            response = requests.get(endpoint, params=params, timeout=30)
            print(f"DEBUG: Namecheap API URL: {response.url}")
            print(f"DEBUG: Response status: {response.status_code}")
            print(f"DEBUG: Response text: {response.text[:500]}...")
        except Exception as e:
            raise RuntimeError(f"Lỗi kết nối Namecheap API: {e}")

        if response.status_code != 200:
            raise RuntimeError(f"Namecheap API trả về HTTP {response.status_code}: {response.text}")

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            raise RuntimeError(f"Namecheap API trả về XML không hợp lệ: {exc}. Response: {response.text}")

        errors = find_elements_by_local_name(root, "Error")
        unsupported_domains: list[str] = []
        for error in errors:
            if error.text:
                unsupported_domains.extend(extract_tld_error_domains(error.text))

        results = find_elements_by_local_name(root, "DomainCheckResult")
        if not results:
            if unsupported_domains:
                remaining = [domain for domain in batch if domain not in unsupported_domains]
                print(f"DEBUG: Skip unsupported TLD domains: {unsupported_domains}")
                if remaining:
                    check_batch(remaining)
                return
            raise RuntimeError("Namecheap API không trả về kết quả kiểm tra domain. Response: " + response.text)

        if unsupported_domains:
            print(f"DEBUG: Skip unsupported TLD domains while keeping valid results: {unsupported_domains}")

        for element in results:
            available_value = element.get("Available", "").strip().lower()
            domain_name = element.get("Domain", "").strip()
            print(f"DEBUG: Domain {domain_name} - Available: {available_value}")
            if available_value == "true" and domain_name:
                available_domains.append(domain_name)

    for start in range(0, len(domains), 50):
        check_batch(domains[start:start + 50])

    return available_domains


def extract_price_from_html(html: str) -> str | None:
    match = re.search(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", html)
    if match:
        return match.group(0).replace(" ", "")
    return None


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
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
        ".scrollable-log { max-height: 800px; overflow-y: auto; white-space: pre-wrap; background: #ffffff; padding: 0.75rem; border-radius: 0.75rem; border: 1px solid #d9e2ef; color: #0b2f5f; font-family: ui-monospace, SFMono-Regular, Consolas, Liberation Mono, Menlo, monospace; }"
        "</style>"
        "<div style='padding: 1rem; border-radius: 16px; margin-bottom: 1rem; background: linear-gradient(135deg, #d7e8ff 0%, #b3d4ff 100%);'>"
        "<h1 style='color:#0b3c78; margin:0;'>Tool Lọc & Săn Domain</h1>"
        "<p style='color:#0f4c81; margin:0.3rem 0 0;'>Kiểm tra khả dụng qua Namecheap API trước, rồi xác định lịch sử lưu trữ domain.</p>"
        "</div>"
        , unsafe_allow_html=True,
    )

    st.sidebar.header("Cấu hình Namecheap API")
    api_user_default = get_secret_value("api_user", "dirseo099")
    user_name_default = get_secret_value("user_name", "")
    api_key_default = get_secret_value("api_key", "")
    client_ip_default = get_secret_value("client_ip", "119.76.33.174")

    api_user = st.sidebar.text_input("ApiUser", value=api_user_default)
    user_name = st.sidebar.text_input("UserName", value=user_name_default)
    api_key = st.sidebar.text_input("ApiKey", value=api_key_default, type="password")
    client_ip = st.sidebar.text_input("ClientIp", value=client_ip_default)
    st.sidebar.markdown(
        "Nhập ApiUser, UserName, ApiKey và ClientIp trước khi chạy. "
        "Nếu UserName trùng với ApiUser thì để trống. ClientIp phải được whitelist trong Namecheap."
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

    left_col, right_col = st.columns(2)
    with left_col:
        st.subheader("Danh sách domain có thể mua")
        left_placeholder = st.empty()
    with right_col:
        st.subheader("Kết quả lịch sử")
        right_placeholder = st.empty()

    log_expander = st.expander("Log chi tiết", expanded=False)
    log_html = log_expander.empty()
    log_messages: list[str] = []

    def append_log(message: str) -> None:
        log_messages.append(message)
        status_area.text(message)
        formatted = escape_html("\n".join(log_messages))
        log_html.markdown(
            f"<div class='scrollable-log'><pre>{formatted}</pre></div>",
            unsafe_allow_html=True,
        )

    if start_button:
        domains = get_domains_from_input(domain_input)
        if not domains:
            st.warning("Vui lòng dán ít nhất một domain vào danh sách.")
            progress_bar.progress(0)
            return

        if not api_user.strip() or not api_key.strip() or not client_ip.strip():
            st.warning("Vui lòng nhập ApiUser, ApiKey và ClientIp trong Sidebar trước khi chạy.")
            progress_bar.progress(0)
            return

        try:
            progress_bar.progress(5)
            append_log("Bắt đầu kiểm tra khả dụng qua Namecheap API...")

            available_domains = check_namecheap_api(
                domains,
                api_user.strip(),
                api_key.strip(),
                user_name.strip(),
                client_ip.strip(),
            )

            for index, domain in enumerate(domains, start=1):
                progress_bar.progress(int(5 + (index / len(domains)) * 40))
                status_area.text(f"[Namecheap] Đang kiểm tra {index}/{len(domains)}: {domain}")
                if domain in available_domains:
                    append_log(f"[Namecheap] Có thể mua: {domain}")
                else:
                    append_log(f"[Namecheap] Không thể mua: {domain}")

            if available_domains:
                left_placeholder.dataframe({
                    "Domain": available_domains,
                })
            else:
                left_placeholder.info("Không có domain nào có thể mua được.")

            progress_bar.progress(50)
            append_log("Hoàn tất kiểm tra Namecheap API.")

            if available_domains:
                right_placeholder.info("Đang chờ hoàn thành kiểm tra Namecheap để bắt đầu kiểm tra lịch sử...")
                archive_results = []
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
