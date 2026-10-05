"""Run an authenticated upload/processing/download/logout deployment smoke test."""
import argparse
import getpass
from io import BytesIO
import time
import urllib.parse

import httpx


def synthetic_pdf():
    """A readable, fictional three-page PDF to verify the LlamaParse allowance."""
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    for number in range(1, 4):
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f'BT /F1 14 Tf 50 700 Td (Synthetic admissions deployment test - page {number}.) Tj 0 -25 Td (Fictional applicant. This is not a real admission record.) Tj ET'.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--email', required=True)
    parser.add_argument('--frontend-url', help='Frontend default URL or the verified custom domain')
    parser.add_argument('--database-dialect', choices=['mssql', 'postgresql'], default='mssql')
    parser.add_argument('--workspace-id', help='University workspace to use for the smoke test')
    parser.add_argument('--live-ocr', action='store_true', help='Parse a fictional 3-page PDF; uses provider credits')
    args = parser.parse_args()
    if urllib.parse.urlparse(args.url).scheme != 'https':
        parser.error('--url must use HTTPS')
    if args.frontend_url and urllib.parse.urlparse(args.frontend_url).scheme != 'https':
        parser.error('--frontend-url must use HTTPS')
    password = getpass.getpass('Application administrator password: ')
    with httpx.Client(base_url=args.url.rstrip('/'), timeout=120) as client:
        response = client.get('/health')
        response.raise_for_status()
        if args.frontend_url:
            response = client.options('/api/ops/dashboard', headers={
                'Origin': args.frontend_url.rstrip('/'), 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'authorization,x-workspace-id,content-type'})
            response.raise_for_status()
            assert response.headers['access-control-allow-origin'] == args.frontend_url.rstrip('/')
            response = httpx.get(args.frontend_url, timeout=120)
            response.raise_for_status()
        assert client.get('/api/jobs/status').status_code == 401
        response = client.post('/api/auth/login', json={'email': args.email, 'password': password})
        response.raise_for_status()
        token = response.json()['token']
        client.headers['Authorization'] = 'Bearer ' + token
        if args.workspace_id:
            client.headers['X-Workspace-Id'] = args.workspace_id
        try:
            response = client.get('/api/rubrics/diagnostics/storage')
            response.raise_for_status()
            assert response.json()['database_dialect'] == args.database_dialect
            response = client.post('/api/upload', files={
                'file': ('deployment-smoke.pdf', synthetic_pdf(), 'application/pdf')
            })
            response.raise_for_status()
            job_id = response.json()['job_id']
            for _ in range(60):
                response = client.get('/api/jobs/' + job_id)
                response.raise_for_status()
                job = response.json()
                if job['status'] == 'failed':
                    raise RuntimeError('Smoke upload processing failed; inspect protected job status.')
                if job['status'] == 'completed':
                    assert job['parser_mode'] in ({'azure', 'live'} if args.live_ocr else {'mock'}), 'Select --live-ocr for the LlamaParse deployment.'
                    response = client.get('/api/jobs/' + job_id + '/record')
                    response.raise_for_status()
                    if args.live_ocr:
                        assert job['page_count'] == 3, 'All three pages must be processed.'
                    print('PASS: HTTPS, authentication, database, private upload, processing and record download.')
                    break
                time.sleep(2)
            else:
                raise RuntimeError('Processing timed out after two minutes.')
        finally:
            response = client.post('/api/auth/logout')
            response.raise_for_status()
            assert client.get('/api/jobs/status').status_code == 401
    print('PASS: logout invalidates the session. Synthetic smoke records remain in the database.')


if __name__ == '__main__':
    main()
