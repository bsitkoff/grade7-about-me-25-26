# Grade 7 About Me Project Publisher (25-26)

This tool downloads seventh grade "About Me" web design projects from Codio and publishes them as a single GitHub Pages site with privacy-friendly display names.

## Features

✅ **Complete Pipeline** - Download, build, publish, and validate in one command  
✅ **Privacy-Friendly** - Display student names as "First LastInitial"  
✅ **Image Inclusion** - Downloads ALL files including images and assets  
✅ **GitHub Pages** - Automatic deployment to public GitHub Pages site  
✅ **Link Validation** - Tests all project links after deployment  
✅ **Section Organization** - Groups students by class section (7-1, 7-2, etc.)  
✅ **Responsive Design** - Mobile-friendly index page with clean styling  

## Quick Start

### 1. Environment Setup

```bash
# Set Codio API credentials
export CODIO_CLIENT_ID=QA1s4NhLJz43a4i3YKyt0EWO  
export CODIO_CLIENT_SECRET=nRq2gMPMz4ccNoimEUXInsoI

# Ensure GitHub CLI is authenticated
gh auth status
```

### 2. Run the Complete Pipeline

```bash
# Download all projects, build site, publish to GitHub Pages, and validate
./bin/publish_about_me_25_26 all
```

That's it! The site will be available at: https://bsitkoff.github.io/grade7-about-me-25-26

## Individual Commands

You can also run individual stages:

```bash
./bin/publish_about_me_25_26 download   # Download projects from Codio only
./bin/publish_about_me_25_26 build      # Build static site only  
./bin/publish_about_me_25_26 publish    # Publish to GitHub Pages only
./bin/publish_about_me_25_26 validate   # Validate deployed links only
```

## How It Works

### 1. Download Phase
- Connects to Codio API using your credentials
- Finds the "About Me" assignment in each 7th grade section
- Downloads ALL files (including images) for each student
- Creates privacy-friendly display names ("First L")
- Saves metadata in `build/manifest.json`

### 2. Build Phase  
- Copies student projects to the site directory
- Generates a responsive index page organized by section
- Creates `.nojekyll` file for GitHub Pages compatibility

### 3. Publish Phase
- Uses `ghp-import` to deploy to the `gh-pages` branch
- Creates/updates the GitHub repository automatically
- Enables GitHub Pages if needed

### 4. Validation Phase
- Waits for GitHub Pages deployment to complete
- Tests each student project link
- Generates validation reports in `site/reports/`

## Project Structure

```
grade7-about-me-25-26/
├── config/
│   └── about_me_25_26.yaml          # Configuration file
├── scripts/
│   ├── publish_about_me.py           # Main pipeline script
│   └── codio_downloader_images.py    # Modified Codio downloader
├── templates/
│   └── index.html.j2                 # HTML template for index page
├── bin/
│   └── publish_about_me_25_26        # Wrapper script
├── build/                            # Downloaded projects (gitignored)
├── site/                             # Generated website (gitignored)
├── logs/                             # Pipeline logs (gitignored)
└── .venv/                            # Python environment (gitignored)
```

## Configuration

The `config/about_me_25_26.yaml` file contains:

- **Course Mappings**: Maps section names (7-1, 7-2, etc.) to Codio course IDs
- **GitHub Settings**: Repository owner, name, and branch for GitHub Pages
- **Display Settings**: How to format student names and organize the site
- **Exclusion Patterns**: Files to skip during download (system files only)

## Requirements

- **Python 3.8+** with virtual environment
- **zstd** compression tool (`brew install zstd` on macOS)
- **GitHub CLI** authenticated with appropriate permissions
- **Codio API** credentials with access to course data

## Troubleshooting

### Common Issues

**"Assignment 'About Me' not found"**  
- Check that the assignment exists in all configured sections
- Verify the assignment name matches exactly (case-insensitive)

**"Authentication failed"**  
- Verify `CODIO_CLIENT_ID` and `CODIO_CLIENT_SECRET` are set correctly
- Check that the credentials have access to the target courses

**"zstd not found"**  
- Install with: `brew install zstd` (macOS) or appropriate package manager

**"GitHub Pages deployment failed"**  
- Check `gh auth status` and ensure you have repository permissions
- Verify the repository name in config matches your GitHub username

### Logs and Reports

- **Pipeline logs**: `logs/publish.log` (rotating, 5MB max)
- **Validation reports**: `site/reports/validation_report.json` and `.txt`  
- **Student manifest**: `build/manifest.json` (contains all student metadata)

## Privacy and Security

- Student names are displayed as "First LastInitial" for privacy
- Full names are only stored in local files, not published
- The site is public on GitHub Pages (no authentication)
- Codio credentials are not stored in the repository

## Annual Rollover

For the next school year:

1. Copy this project to a new directory (e.g., `grade7-about-me-26-27`)
2. Update `config/about_me_26_27.yaml`:
   - Change `school_year` to "26-27"
   - Update `site_title` and `repo` name
   - Update course IDs for new sections
3. Update the wrapper script and configuration paths
4. Run the pipeline for the new year's projects

## Technical Details

- **Codio Integration**: Uses the official Codio REST API with rate limiting
- **Archive Handling**: Supports `.zst` compressed archives from Codio
- **Concurrent Downloads**: Configurable concurrency for faster processing
- **Retry Logic**: Automatic retries with exponential backoff for reliability
- **Binary Safe**: Properly handles images, fonts, and other binary assets
- **GitHub Pages**: Uses `ghp-import` for reliable deployment

## Site URL

The published site will be available at:
**https://bsitkoff.github.io/grade7-about-me-25-26**