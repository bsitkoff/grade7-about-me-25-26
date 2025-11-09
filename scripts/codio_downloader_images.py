#!/usr/bin/env python3
"""
Modified Codio Assignment Downloader for About Me Projects (25-26)

Based on the original codio-downloader.py but modified to:
- Include ALL files (especially images) instead of excluding them
- Support configuration-driven exclusion patterns
- Provide better extraction and binary file handling
- Include retry logic with tenacity

This version is specifically designed for web projects where images and assets are critical.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    import requests
    from dotenv import load_dotenv
    from tenacity import retry, stop_after_attempt, wait_exponential
except ImportError as e:
    print(f"Error: Missing required library: {e}")
    print("Install with: pip install requests python-dotenv tenacity")
    sys.exit(1)


# ============================================================================
# Configuration and Logging
# ============================================================================

class Config:
    """Global configuration"""
    CODIO_DOMAIN = "codio.com"
    API_BASE_URL = f"https://octopus.{CODIO_DOMAIN}/api/v1"
    OAUTH_URL = f"https://oauth.{CODIO_DOMAIN}/api/v1/token"
    
    # Rate limits from Codio API docs
    BURST_RATE_LIMIT = 50  # requests per 10 seconds
    BURST_WINDOW = 10  # seconds
    DAILY_LIMIT = 10000  # requests per day
    
    # Retry configuration
    MAX_RETRIES = 5
    RETRY_BACKOFF_BASE = 0.5  # seconds
    
    # Token expiry buffer (refresh 5 minutes before actual expiry)
    TOKEN_EXPIRY_BUFFER = 300  # seconds


# ============================================================================
# Rate Limiter
# ============================================================================

class RateLimiter:
    """Token bucket rate limiter for Codio API"""
    
    def __init__(self, burst_limit: int = Config.BURST_RATE_LIMIT, 
                 window: int = Config.BURST_WINDOW,
                 daily_limit: int = Config.DAILY_LIMIT):
        self.burst_limit = burst_limit
        self.window = window
        self.daily_limit = daily_limit
        
        self.request_times = deque()
        self.daily_count = 0
        self.daily_reset_time = time.time() + 86400  # 24 hours from now
        
        self.logger = logging.getLogger('codio_downloader.rate_limiter')
    
    def wait_if_needed(self):
        """Block if rate limit would be exceeded"""
        now = time.time()
        
        # Reset daily counter if needed
        if now >= self.daily_reset_time:
            self.daily_count = 0
            self.daily_reset_time = now + 86400
            self.logger.info("Daily rate limit counter reset")
        
        # Check daily limit
        if self.daily_count >= self.daily_limit:
            wait_time = self.daily_reset_time - now
            self.logger.warning(
                f"Daily limit ({self.daily_limit}) reached. "
                f"Waiting {wait_time:.0f}s until reset."
            )
            time.sleep(wait_time)
            self.daily_count = 0
            self.daily_reset_time = time.time() + 86400
        
        # Remove old requests outside the window
        cutoff = now - self.window
        while self.request_times and self.request_times[0] < cutoff:
            self.request_times.popleft()
        
        # Check burst limit
        if len(self.request_times) >= self.burst_limit:
            wait_time = self.request_times[0] + self.window - now
            if wait_time > 0:
                self.logger.debug(
                    f"Burst limit reached. Waiting {wait_time:.2f}s"
                )
                time.sleep(wait_time + 0.1)  # Add small buffer
                # Clean up old requests again after waiting
                now = time.time()
                cutoff = now - self.window
                while self.request_times and self.request_times[0] < cutoff:
                    self.request_times.popleft()
        
        # Record this request
        self.request_times.append(time.time())
        self.daily_count += 1


# ============================================================================
# Codio API Client (Modified for Images)
# ============================================================================

class CodioAPI:
    """Client for Codio REST API - Modified to include all files"""
    
    def __init__(self, client_id: str, client_secret: str, dry_run: bool = False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.dry_run = dry_run
        
        self.access_token: Optional[str] = None
        self.token_expiry: float = 0
        
        self.session = requests.Session()
        self.rate_limiter = RateLimiter()
        self.logger = logging.getLogger('codio_downloader.api')
        
        if not dry_run:
            self.authenticate()
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def authenticate(self):
        """Authenticate and get access token"""
        if self.dry_run:
            self.logger.info("[DRY RUN] Would authenticate with Codio")
            return
        
        self.logger.info("Authenticating with Codio API")
        
        params = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.client_secret
        }
        
        try:
            response = self.session.get(
                Config.OAUTH_URL,
                params=params,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            
            self.access_token = data['access_token']
            # Tokens typically expire in 1 hour, set expiry with buffer
            self.token_expiry = time.time() + 3600 - Config.TOKEN_EXPIRY_BUFFER
            
            self.logger.info("Successfully authenticated with Codio")
        except Exception as e:
            self.logger.error(f"Authentication failed: {e}")
            raise
    
    def _ensure_authenticated(self):
        """Refresh token if needed"""
        if time.time() >= self.token_expiry:
            self.logger.info("Token expired, re-authenticating")
            self.authenticate()
    
    @retry(stop=stop_after_attempt(Config.MAX_RETRIES), 
           wait=wait_exponential(multiplier=Config.RETRY_BACKOFF_BASE, min=4, max=60))
    def request(self, method: str, path: str, params: Optional[Dict] = None,
                json_data: Optional[Dict] = None, stream: bool = False) -> Any:
        """Make an authenticated API request with retry logic"""
        if self.dry_run:
            self.logger.debug(f"[DRY RUN] Would {method} {path}")
            return {} if not stream else None
        
        self._ensure_authenticated()
        self.rate_limiter.wait_if_needed()
        
        url = f"{Config.API_BASE_URL}/{path.lstrip('/')}"
        headers = {'Authorization': f'Bearer {self.access_token}'}
        
        response = self.session.request(
            method,
            url,
            params=params,
            json=json_data,
            headers=headers,
            stream=stream,
            timeout=120 if not stream else None
        )
        
        # Handle 401 (refresh token and retry will be handled by tenacity)
        if response.status_code == 401:
            self.logger.warning("Got 401, refreshing token")
            self.authenticate()
            headers = {'Authorization': f'Bearer {self.access_token}'}
            response = self.session.request(
                method,
                url,
                params=params,
                json=json_data,
                headers=headers,
                stream=stream,
                timeout=120 if not stream else None
            )
        
        # Handle 429 (rate limit)
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 10))
            self.logger.warning(f"Rate limited, waiting {retry_after}s")
            time.sleep(retry_after)
            raise requests.exceptions.RequestException("Rate limited, retrying")
        
        response.raise_for_status()
        
        if stream:
            return response
        else:
            return response.json() if response.content else {}
    
    def get_course(self, course_id: str) -> Dict:
        """Get course information including assignments"""
        self.logger.info(f"Fetching course info for {course_id}")
        return self.request('GET', f'/courses/{course_id}', 
                          params={'withHiddenAssignments': 'true'})
    
    def get_students(self, course_id: str) -> List[Dict]:
        """Get list of students in a course"""
        self.logger.info(f"Fetching students for course {course_id}")
        return self.request('GET', f'/courses/{course_id}/students')
    
    def export_student_assignment(self, course_id: str, assignment_id: str, 
                                 student_id: str) -> str:
        """Export student assignment (returns download URL after polling)"""
        self.logger.debug(
            f"Exporting assignment {assignment_id} for student {student_id}"
        )
        
        # Initiate export
        result = self.request(
            'GET',
            f'/courses/{course_id}/assignments/{assignment_id}/students/{student_id}/download'
        )
        
        task_uri = result.get('taskUri')
        if not task_uri:
            raise ValueError("No taskUri in export response")
        
        # Poll until ready
        return self._wait_download_task(task_uri)
    
    def download_student_assignment(self, course_id: str, assignment_id: str,
                                   student_id: str, dest_path: Path):
        """Download and extract student assignment to directory"""
        # Get download URL
        url = self.export_student_assignment(course_id, assignment_id, student_id)
        
        # Download to temporary file
        temp_file = dest_path.with_suffix('.zst')
        self._download_file(url, temp_file)
        
        # Extract to final destination
        self._extract_assignment(temp_file, dest_path)
        
        # Cleanup
        if temp_file.exists():
            temp_file.unlink()
    
    def _wait_download_task(self, task_uri: str, max_wait: int = 300) -> str:
        """Poll a download task until complete"""
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            result = self.request('GET', task_uri.replace(Config.API_BASE_URL + '/', ''))
            
            if result.get('done'):
                if result.get('error'):
                    raise RuntimeError(f"Export task failed: {result['error']}")
                return result['url']
            
            time.sleep(0.5)
        
        raise TimeoutError(f"Task {task_uri} did not complete within {max_wait}s")
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def _download_file(self, url: str, dest_path: Path):
        """Download a file from URL"""
        self.logger.debug(f"Downloading {url} to {dest_path}")
        
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Direct download (not through API, no auth needed)
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        self.logger.debug(f"Download complete: {dest_path}")
    
    def _extract_assignment(self, zst_file: Path, dest_dir: Path):
        """Extract assignment archive (supports .zst and .tar files) - INCLUDES ALL FILES"""
        self.logger.debug(f"Extracting {zst_file} to {dest_dir}")
        
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Handle .zst files (need decompression first)
        if zst_file.suffix == '.zst':
            tar_file = zst_file.with_suffix('.tar')
            self._decompress_zstd(zst_file, tar_file)
            self._extract_tar_inclusive(tar_file, dest_dir)
            if tar_file.exists():
                tar_file.unlink()
        elif zst_file.suffix == '.tar':
            self._extract_tar_inclusive(zst_file, dest_dir)
        else:
            raise ValueError(f"Unsupported archive format: {zst_file.suffix}")
    
    def _decompress_zstd(self, zst_path: Path, tar_path: Path):
        """Decompress .zst file to .tar"""
        if self.dry_run:
            self.logger.debug(f"[DRY RUN] Would decompress {zst_path} to {tar_path}")
            return
        
        # Try homebrew path first, then PATH
        zstd_cmd = '/opt/homebrew/bin/zstd'
        if not Path(zstd_cmd).exists():
            zstd_cmd = 'zstd'
        
        try:
            subprocess.run(
                [zstd_cmd, '-d', str(zst_path), '-o', str(tar_path)],
                check=True,
                capture_output=True
            )
            self.logger.debug(f"Decompressed {zst_path.name}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Decompression failed: {e.stderr.decode()}")
            raise
        except FileNotFoundError:
            self.logger.error("zstd not found. Install with: brew install zstd")
            raise
    
    def _extract_tar_inclusive(self, tar_path: Path, dest_dir: Path, exclude_globs: List[str] = None):
        """Extract tar file including ALL files (images, etc.) with optional exclusions"""
        if self.dry_run:
            self.logger.debug(f"[DRY RUN] Would extract {tar_path} to {dest_dir}")
            return
        
        if exclude_globs is None:
            exclude_globs = ['.git', '.guides', '.codio']  # Only exclude system files
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        with tarfile.open(tar_path, 'r') as tar:
            for member in tar.getmembers():
                # Security check for path traversal
                member_path = Path(dest_dir) / member.name
                if not member_path.resolve().is_relative_to(dest_dir.resolve()):
                    self.logger.warning(f"Skipping potentially unsafe path: {member.name}")
                    continue
                
                # Check exclusions
                should_exclude = False
                for pattern in exclude_globs:
                    if pattern in member.name or member.name.startswith(pattern):
                        should_exclude = True
                        break
                
                if should_exclude:
                    self.logger.debug(f"Excluding file: {member.name}")
                    continue
                
                # Extract everything else (including images, CSS, JS, etc.)
                try:
                    tar.extract(member, dest_dir)
                except Exception as e:
                    self.logger.warning(f"Failed to extract {member.name}: {e}")
                    continue
        
        self.logger.debug(f"Extracted {tar_path.name} (including all assets)")


# Export the classes needed by the main script
__all__ = ['CodioAPI', 'Config']