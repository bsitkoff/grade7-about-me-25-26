# Preferred name mappings for privacy-friendly display
PREFERRED_NAMES = {
    "Alexander": "Alex",
    "Ann-Tarah": "Annie",
    "Finnegan": "Finn", 
    "Jonathan": "Julian",
    "Nicolas": "Nico",
    "Shiyang": "April"
}

# Special case for Michael H (since there might be other Michaels)
SPECIAL_NAME_CASES = {
    "Michael Holland": "Micky H",
    "Michael H": "Micky H"  # Handle both full name and abbreviated forms
}


def parse_display_name(full_name: str) -> str:
    """Parse full name into 'First LastInitial' format with preferred names for privacy"""
    if not full_name or not full_name.strip():
        return "Unknown Student"
    
    # Handle special cases first (full name matches)
    if full_name.strip() in SPECIAL_NAME_CASES:
        return SPECIAL_NAME_CASES[full_name.strip()]
    
    parts = full_name.strip().split()
    
    if len(parts) == 1:
        # Use preferred name if available
        first = PREFERRED_NAMES.get(parts[0], parts[0])
        return first
    
    first = parts[0]
    # Handle multi-part last names by taking the first letter of the final part
    last_initial = parts[-1][0].upper() if parts[-1] else ""
    
    # Use preferred name if available
    preferred_first = PREFERRED_NAMES.get(first, first)
    
    return f"{preferred_first} {last_initial}" if last_initial else preferred_first

#!/usr/bin/env python3
"""
About Me Project Publisher for Grade 7 (25-26)

Downloads student About Me projects from Codio and publishes them as a single
GitHub Pages site with privacy-friendly display names.

Usage:
    python scripts/publish_about_me.py --config config/about_me_25_26.yaml all
    python scripts/publish_about_me.py --config config/about_me_25_26.yaml download
    python scripts/publish_about_me.py --config config/about_me_25_26.yaml build
    python scripts/publish_about_me.py --config config/about_me_25_26.yaml publish
    python scripts/publish_about_me.py --config config/about_me_25_26.yaml validate
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
import yaml
from jinja2 import Environment, FileSystemLoader
from tenacity import retry, stop_after_attempt, wait_exponential
from tqdm import tqdm

# Import our modified Codio downloader
sys.path.append(str(Path(__file__).parent))
from codio_downloader_images import CodioAPI, Config as CodioConfig


class PublishConfig:
    """Configuration for the publishing pipeline"""
    
    def __init__(self, config_path: Path):
        with open(config_path, 'r') as f:
            self.data = yaml.safe_load(f)
        
        self.config_dir = config_path.parent
        self.project_root = config_path.parent.parent
        
        # Validate required sections
        required_sections = ['school_year', 'site_title', 'assignment_name', 'sections', 'github']
        for section in required_sections:
            if section not in self.data:
                raise ValueError(f"Missing required config section: {section}")
    
    @property
    def school_year(self) -> str:
        return self.data['school_year']
    
    @property
    def site_title(self) -> str:
        return self.data['site_title']
    
    @property
    def assignment_name(self) -> str:
        return self.data['assignment_name']
    
    @property
    def sections(self) -> Dict[str, str]:
        return self.data['sections']
    
    @property
    def github_config(self) -> Dict[str, str]:
        return self.data['github']
    
    @property
    def pages_base_url(self) -> str:
        return self.data.get('pages_base_url', f"https://{self.github_config['owner']}.github.io/{self.github_config['repo']}")
    
    @property
    def build_dir(self) -> Path:
        return self.project_root / self.data.get('build_dir', 'build')
    
    @property
    def site_dir(self) -> Path:
        return self.project_root / self.data.get('output_dir', 'site')
    
    @property
    def templates_dir(self) -> Path:
        return self.project_root / 'templates'
    
    @property
    def exclude_globs(self) -> List[str]:
        return self.data.get('exclude_globs', ['.git', '.guides', '.codio'])
    
    @property
    def max_concurrency(self) -> int:
        return self.data.get('max_concurrency', 8)
    
    @property
    def timeouts(self) -> Dict[str, int]:
        return self.data.get('timeouts', {
            'api_seconds': 30,
            'download_seconds': 120,
            'http_seconds': 20
        })


def setup_logging(project_root: Path, verbose: bool = False) -> logging.Logger:
    """Setup logging with both file and console handlers"""
    log_dir = project_root / 'logs'
    log_dir.mkdir(exist_ok=True)
    
    logger = logging.getLogger('publish_about_me')
    logger.setLevel(logging.DEBUG)
    
    # Clear any existing handlers
    logger.handlers = []
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_dir / 'publish.log',
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3
    )
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    return logger


def sanitize_name(name: str) -> str:
    """Sanitize a name for use as filename/directory"""
    sanitized = name.replace(' ', '_')
    sanitized = re.sub(r'[<>:"/\\|?*]', '', sanitized)
    sanitized = sanitized.encode('ascii', 'ignore').decode('ascii')
    return sanitized or 'unknown'


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug"""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text




def find_entry_page(project_dir: Path) -> Optional[Tuple[str, str]]:
    """Find the main entry page (index.html) in a project directory or subdirectories
    Returns (relative_path, filename) or None if not found
    """
    # Search patterns for main page
    patterns = ['index.html', 'Index.html', 'INDEX.HTML', 'index.htm', 'Index.htm']
    fallback_patterns = ['home.html', 'start.html', 'main.html']
    
    # First check the root directory
    for pattern in patterns:
        entry_file = project_dir / pattern
        if entry_file.exists() and entry_file.is_file():
            return ("", pattern)  # Empty path means root
    
    # Then search subdirectories (up to 2 levels deep)
    for subdir in project_dir.iterdir():
        if subdir.is_dir() and not subdir.name.startswith('.'):
            for pattern in patterns:
                entry_file = subdir / pattern
                if entry_file.exists() and entry_file.is_file():
                    return (subdir.name, pattern)
            
            # Check one level deeper
            for subsubdir in subdir.iterdir():
                if subsubdir.is_dir() and not subsubdir.name.startswith('.'):
                    for pattern in patterns:
                        entry_file = subsubdir / pattern
                        if entry_file.exists() and entry_file.is_file():
                            return (f"{subdir.name}/{subsubdir.name}", pattern)
    
    # Fallback patterns in root only
    for pattern in fallback_patterns:
        entry_file = project_dir / pattern
        if entry_file.exists() and entry_file.is_file():
            return ("", pattern)
    
    return None


class AboutMeDownloader:
    """Downloads About Me projects from Codio with images included"""
    
    def __init__(self, config: PublishConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        
        # Initialize Codio API
        client_id = os.getenv('CODIO_CLIENT_ID')
        client_secret = os.getenv('CODIO_CLIENT_SECRET')
        
        if not client_id or not client_secret:
            raise ValueError("CODIO_CLIENT_ID and CODIO_CLIENT_SECRET environment variables must be set")
        
        self.codio_api = CodioAPI(client_id, client_secret, dry_run=False)
        self.manifest = []
    
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
    def download_student_project(self, section: str, student: Dict, assignment_id: str, course_id: str) -> Dict:
        """Download a single student's project"""
        student_name = student['name']
        student_id = student['id']
        
        # Determine username and slug
        username = student.get('username', '')
        if not username:
            username = slugify(student_name)
        
        student_slug = sanitize_name(username)
        display_name = parse_display_name(student_name)
        
        # Set up directories
        section_dir = self.config.build_dir / section
        student_dir = section_dir / student_slug
        
        self.logger.info(f"Downloading {student_name} ({section}) -> {student_slug}")
        
        try:
            # Clean up any existing files
            if student_dir.exists():
                shutil.rmtree(student_dir)
            
            student_dir.mkdir(parents=True, exist_ok=True)
            
            # Download using Codio API - this downloads and extracts to student_dir
            self.codio_api.download_student_assignment(
                course_id, assignment_id, student_id, student_dir
            )
            
            # Find entry page
            entry_page_result = find_entry_page(student_dir)
            warnings = []
            entry_page_path = None
            entry_page_file = None
            
            if entry_page_result:
                entry_page_path, entry_page_file = entry_page_result
                full_entry_path = f"{entry_page_path}/{entry_page_file}" if entry_page_path else entry_page_file
                self.logger.debug(f"Found entry page for {student_name}: {full_entry_path}")
            else:
                warnings.append("No index.html or entry page found")
                self.logger.warning(f"No entry page found for {student_name}")
            
            # Create student metadata
            student_meta = {
                'section': section,
                'full_name': student_name,
                'display_name_short': display_name,
                'username': username,
                'slug': student_slug,
                'codio_id': student_id,
                'local_path': str(student_dir.relative_to(self.config.project_root)),
                'entry_page': full_entry_path if entry_page_result else None,
                'entry_page_path': entry_page_path,
                'entry_page_file': entry_page_file,
                'warnings': warnings,
                'download_timestamp': time.time()
            }
            
            return student_meta
            
        except Exception as e:
            error_msg = f"Failed to download {student_name}: {str(e)}"
            self.logger.error(error_msg)
            
            return {
                'section': section,
                'full_name': student_name,
                'display_name_short': display_name,
                'username': username,
                'slug': student_slug,
                'codio_id': student_id,
                'local_path': None,
                'entry_page': None,
                'entry_page_path': None,
                'entry_page_file': None,
                'warnings': [],
                'errors': [error_msg],
                'download_timestamp': time.time()
            }
    
    def download_all_students(self) -> List[Dict]:
        """Download all student projects from all sections"""
        self.logger.info("Starting download of all student projects")
        
        # Clean build directory
        if self.config.build_dir.exists():
            shutil.rmtree(self.config.build_dir)
        self.config.build_dir.mkdir(parents=True, exist_ok=True)
        
        all_tasks = []
        
        # Gather all download tasks
        for section, course_id in self.config.sections.items():
            self.logger.info(f"Processing section {section} (course: {course_id})")
            
            try:
                # Get course info
                course = self.codio_api.get_course(course_id)
                course_name = course['name']
                self.logger.info(f"Course: {course_name}")
                
                # Find the About Me assignment
                assignment = None
                for module in course.get('modules', []):
                    for a in module.get('assignments', []):
                        if a['name'].lower() == self.config.assignment_name.lower():
                            assignment = a
                            break
                    if assignment:
                        break
                
                if not assignment:
                    self.logger.error(f"Assignment '{self.config.assignment_name}' not found in section {section}")
                    continue
                
                assignment_id = assignment['id']
                self.logger.info(f"Found assignment: {assignment['name']} (ID: {assignment_id})")
                
                # Get students
                students = self.codio_api.get_students(course_id)
                self.logger.info(f"Found {len(students)} students in section {section}")
                
                # Add download tasks
                for student in students:
                    all_tasks.append((section, student, assignment_id, course_id))
                    
            except Exception as e:
                self.logger.error(f"Failed to process section {section}: {e}")
                continue
        
        self.logger.info(f"Total students to download: {len(all_tasks)}")
        
        # Download with concurrent execution
        results = []
        with ThreadPoolExecutor(max_workers=self.config.max_concurrency) as executor:
            future_to_task = {
                executor.submit(self.download_student_project, section, student, assignment_id, course_id): (section, student['name'])
                for section, student, assignment_id, course_id in all_tasks
            }
            
            with tqdm(total=len(all_tasks), desc="Downloading projects") as pbar:
                for future in as_completed(future_to_task):
                    section, student_name = future_to_task[future]
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        self.logger.error(f"Download task failed for {student_name} ({section}): {e}")
                    finally:
                        pbar.update(1)
        
        # Write manifest
        manifest_path = self.config.build_dir / 'manifest.json'
        with open(manifest_path, 'w') as f:
            json.dump(results, f, indent=2)
        
        self.logger.info(f"Downloaded {len(results)} student projects")
        return results


class SiteBuilder:
    """Builds the static site from downloaded projects"""
    
    def __init__(self, config: PublishConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        
        # Setup Jinja2 environment
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.config.templates_dir)),
            autoescape=True
        )
    
    def copy_student_projects(self, manifest: List[Dict]) -> None:
        """Copy student projects from build to site directory"""
        self.logger.info("Copying student projects to site directory")
        
        # Clean site directory
        if self.config.site_dir.exists():
            shutil.rmtree(self.config.site_dir)
        self.config.site_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy each student's project
        for student in tqdm(manifest, desc="Copying projects"):
            if not student.get('local_path') or 'errors' in student:
                continue
                
            source_dir = self.config.project_root / student['local_path']
            dest_dir = self.config.site_dir / student['section'] / student['slug']
            
            if source_dir.exists():
                dest_dir.parent.mkdir(parents=True, exist_ok=True)
                
                # If there's an entry page in a subdirectory, copy from that subdirectory
                # Otherwise copy the entire student directory
                if student.get('entry_page_path') and student.get('entry_page_path') != '':
                    # Copy from the subdirectory containing the project
                    project_source = source_dir / student['entry_page_path']
                    if project_source.exists():
                        shutil.copytree(project_source, dest_dir, dirs_exist_ok=True)
                        self.logger.debug(f"Copied project from {project_source} to {dest_dir}")
                    else:
                        # Fallback: copy entire directory
                        shutil.copytree(source_dir, dest_dir, dirs_exist_ok=True)
                else:
                    # Copy entire directory (entry page is in root)
                    shutil.copytree(source_dir, dest_dir, dirs_exist_ok=True)
    
    def build_index_page(self, manifest: List[Dict]) -> None:
        """Build the main index page"""
        self.logger.info("Building main index page")
        
        # Organize students by section
        sections_data = {}
        for student in manifest:
            if 'errors' in student:
                continue
                
            section = student['section']
            if section not in sections_data:
                sections_data[section] = []
            sections_data[section].append(student)
        
        # Sort students within each section (by display name)
        for section in sections_data:
            sections_data[section].sort(key=lambda s: s.get('display_name_short', s.get('full_name', '')))
        
        # Sort sections
        sorted_sections = sorted(sections_data.items())
        
        # Render template
        template = self.jinja_env.get_template('index.html.j2')
        html_content = template.render(
            site_title=self.config.site_title,
            sections=sorted_sections,
            pages_base_url=self.config.pages_base_url,
            generation_time=time.strftime('%B %d, %Y at %I:%M %p')
        )
        
        # Write index.html
        index_path = self.config.site_dir / 'index.html'
        with open(index_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        # Create .nojekyll file
        nojekyll_path = self.config.site_dir / '.nojekyll'
        nojekyll_path.touch()
    
    def build_site(self) -> None:
        """Build the complete site"""
        # Load manifest
        manifest_path = self.config.build_dir / 'manifest.json'
        if not manifest_path.exists():
            raise FileNotFoundError("No manifest.json found. Run download first.")
        
        with open(manifest_path) as f:
            manifest = json.load(f)
        
        # Copy projects and build index
        self.copy_student_projects(manifest)
        self.build_index_page(manifest)
        
        self.logger.info("Site build complete")


class SitePublisher:
    """Publishes the site to GitHub Pages"""
    
    def __init__(self, config: PublishConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
    
    def publish_to_github_pages(self) -> None:
        """Publish site using ghp-import"""
        self.logger.info("Publishing site to GitHub Pages")
        
        if not self.config.site_dir.exists():
            raise FileNotFoundError("Site directory not found. Run build first.")
        
        github_config = self.config.github_config
        commit_message = f"{self.config.school_year}: publish About Me projects"
        
        # Use ghp-import to publish
        cmd = [
            'ghp-import',
            '-n',  # Add .nojekyll
            '-p',  # Push to origin
            '-f',  # Force update
            '-r', f"https://github.com/{github_config['owner']}/{github_config['repo']}.git",
            '-b', github_config['branch'],
            '-m', commit_message,
            str(self.config.site_dir)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode == 0:
                self.logger.info("Successfully published to GitHub Pages")
                self.logger.info(f"Site URL: {self.config.pages_base_url}")
            else:
                self.logger.error(f"ghp-import failed: {result.stderr}")
                raise subprocess.CalledProcessError(result.returncode, cmd, result.stderr)
        except subprocess.TimeoutExpired:
            self.logger.error("Publishing timed out after 5 minutes")
            raise


class SiteValidator:
    """Validates the deployed site"""
    
    def __init__(self, config: PublishConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
    
    @retry(stop=stop_after_attempt(10), wait=wait_exponential(multiplier=1, min=2, max=30))
    def wait_for_deployment(self) -> None:
        """Wait for GitHub Pages deployment to be ready"""
        self.logger.info("Waiting for GitHub Pages deployment...")
        
        response = requests.head(self.config.pages_base_url, timeout=10)
        if response.status_code != 200:
            raise requests.RequestException(f"Site not ready: {response.status_code}")
        
        self.logger.info("Site is deployed and accessible")
    
    def validate_student_links(self) -> Dict:
        """Validate all student project links"""
        self.logger.info("Validating student project links")
        
        # Load manifest
        manifest_path = self.config.build_dir / 'manifest.json'
        with open(manifest_path) as f:
            manifest = json.load(f)
        
        validation_results = {
            'total': 0,
            'passed': 0,
            'failed': 0,
            'missing_entry': 0,
            'details': []
        }
        
        for student in tqdm(manifest, desc="Validating links"):
            if 'errors' in student or not student.get('entry_page_file'):
                validation_results['missing_entry'] += 1
                validation_results['details'].append({
                    'student': student['display_name_short'],
                    'section': student['section'],
                    'status': 'missing_entry',
                    'url': None,
                    'message': 'No entry page found'
                })
                continue
            
            # Build URL
            url = urljoin(
                self.config.pages_base_url + '/',
                f"{student['section']}/{student['slug']}/{student['entry_page_file']}"
            )
            
            validation_results['total'] += 1
            
            try:
                response = requests.head(url, timeout=self.config.timeouts['http_seconds'])
                if 200 <= response.status_code < 400:
                    validation_results['passed'] += 1
                    status = 'pass'
                    message = f"HTTP {response.status_code}"
                else:
                    validation_results['failed'] += 1
                    status = 'fail'
                    message = f"HTTP {response.status_code}"
            except Exception as e:
                validation_results['failed'] += 1
                status = 'fail'
                message = str(e)
            
            validation_results['details'].append({
                'student': student['display_name_short'],
                'section': student['section'],
                'status': status,
                'url': url,
                'message': message
            })
        
        # Save validation report
        reports_dir = self.config.site_dir / 'reports'
        reports_dir.mkdir(exist_ok=True)
        
        with open(reports_dir / 'validation_report.json', 'w') as f:
            json.dump(validation_results, f, indent=2)
        
        # Generate text summary
        summary = f"""Validation Summary for {self.config.site_title}
Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}

Total student projects: {validation_results['total']}
Successful links: {validation_results['passed']}
Failed links: {validation_results['failed']} 
Missing entry pages: {validation_results['missing_entry']}

Site URL: {self.config.pages_base_url}
"""
        
        with open(reports_dir / 'validation_report.txt', 'w') as f:
            f.write(summary)
        
        self.logger.info(f"Validation complete: {validation_results['passed']}/{validation_results['total']} links working")
        
        return validation_results
    
    def validate_site(self) -> None:
        """Run complete site validation"""
        try:
            self.wait_for_deployment()
            self.validate_student_links()
        except Exception as e:
            self.logger.error(f"Validation failed: {e}")
            # Don't raise - validation failures shouldn't stop the pipeline


def main():
    parser = argparse.ArgumentParser(description='Publish Grade 7 About Me projects to GitHub Pages')
    parser.add_argument('--config', type=Path, required=True, help='Configuration file path')
    parser.add_argument('--verbose', '-v', action='store_true', help='Enable verbose logging')
    parser.add_argument('command', choices=['all', 'download', 'build', 'publish', 'validate'],
                       help='Command to run')
    
    args = parser.parse_args()
    
    if not args.config.exists():
        print(f"Error: Config file not found: {args.config}")
        sys.exit(1)
    
    # Load configuration
    config = PublishConfig(args.config)
    logger = setup_logging(config.project_root, args.verbose)
    
    logger.info(f"Starting About Me publisher for {config.school_year}")
    logger.info(f"Command: {args.command}")
    
    try:
        if args.command in ['all', 'download']:
            downloader = AboutMeDownloader(config, logger)
            downloader.download_all_students()
        
        if args.command in ['all', 'build']:
            builder = SiteBuilder(config, logger)
            builder.build_site()
        
        if args.command in ['all', 'publish']:
            publisher = SitePublisher(config, logger)
            publisher.publish_to_github_pages()
        
        if args.command in ['all', 'validate']:
            validator = SiteValidator(config, logger)
            validator.validate_site()
        
        logger.info("Pipeline completed successfully")
        
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()