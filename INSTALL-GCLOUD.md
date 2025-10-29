# Installing Google Cloud SDK

## For macOS

### Option 1: Using Homebrew (Recommended)
```bash
brew install --cask google-cloud-sdk
```

### Option 2: Manual Installation
```bash
# Download the SDK
curl https://sdk.cloud.google.com | bash

# Restart your shell
exec -l $SHELL

# Initialize
gcloud init
```

## For Linux

```bash
# Download and install
curl https://sdk.cloud.google.com | bash

# Initialize
gcloud init
```

## After Installation

1. **Authenticate:**
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```

2. **Set your project:**
   ```bash
   gcloud config set project medical-advisor-bd734
   ```

3. **Verify installation:**
   ```bash
   gcloud --version
   gsutil --version
   ```

## Deploy

Once installed, run:
```bash
./deploy.sh
```
