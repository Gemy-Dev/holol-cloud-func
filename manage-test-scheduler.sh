#!/bin/bash

# Manage Test Notification Scheduler
# Usage: ./manage-test-scheduler.sh [pause|resume|delete|status|trigger]

set -e

# Configuration
PROJECT_ID="holol-cloud-func"
REGION="us-central1"
SCHEDULER_NAME="test-notifications"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
show_usage() {
    echo -e "${BLUE}Test Notification Scheduler Manager${NC}"
    echo ""
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  pause    - Pause the test scheduler (stop sending notifications)"
    echo "  resume   - Resume the test scheduler"
    echo "  delete   - Delete the test scheduler completely"
    echo "  status   - Show scheduler status"
    echo "  trigger  - Manually trigger a test notification now"
    echo "  logs     - Show recent function logs"
    echo ""
    echo "Example:"
    echo "  $0 pause"
    echo ""
}

pause_scheduler() {
    echo -e "${YELLOW}⏸️  Pausing test scheduler...${NC}"
    gcloud scheduler jobs pause $SCHEDULER_NAME \
        --location=$REGION \
        --project=$PROJECT_ID
    echo -e "${GREEN}✅ Test scheduler paused${NC}"
}

resume_scheduler() {
    echo -e "${YELLOW}▶️  Resuming test scheduler...${NC}"
    gcloud scheduler jobs resume $SCHEDULER_NAME \
        --location=$REGION \
        --project=$PROJECT_ID
    echo -e "${GREEN}✅ Test scheduler resumed${NC}"
    echo -e "${YELLOW}⚠️  Notifications will be sent every minute!${NC}"
}

delete_scheduler() {
    echo -e "${RED}🗑️  Deleting test scheduler...${NC}"
    read -p "Are you sure you want to delete the test scheduler? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        gcloud scheduler jobs delete $SCHEDULER_NAME \
            --location=$REGION \
            --project=$PROJECT_ID \
            --quiet
        echo -e "${GREEN}✅ Test scheduler deleted${NC}"
    else
        echo -e "${YELLOW}❌ Cancelled${NC}"
    fi
}

show_status() {
    echo -e "${BLUE}📊 Test Scheduler Status${NC}"
    echo ""
    gcloud scheduler jobs describe $SCHEDULER_NAME \
        --location=$REGION \
        --project=$PROJECT_ID \
        --format="table(
            name,
            schedule,
            state,
            status.lastAttemptTime,
            status.state
        )"
}

trigger_now() {
    echo -e "${YELLOW}🚀 Triggering test notification now...${NC}"
    
    # Get function URL
    FUNCTION_URL=$(gcloud functions describe app \
        --region=$REGION \
        --project=$PROJECT_ID \
        --format="value(serviceConfig.uri)")
    
    if [ -z "$FUNCTION_URL" ]; then
        echo -e "${RED}❌ Failed to get function URL${NC}"
        exit 1
    fi
    
    echo "📡 Function URL: $FUNCTION_URL"
    
    # Trigger the function
    response=$(curl -s -X POST "$FUNCTION_URL" \
        -H "Content-Type: application/json" \
        -d '{"action":"test_notification_to_all"}')
    
    echo ""
    echo -e "${GREEN}✅ Response:${NC}"
    echo "$response" | jq '.' 2>/dev/null || echo "$response"
}

show_logs() {
    echo -e "${BLUE}📋 Recent Function Logs (Test Notifications)${NC}"
    echo ""
    gcloud functions logs read app \
        --region=$REGION \
        --project=$PROJECT_ID \
        --limit=30 | grep -i "test notification" || echo "No test notification logs found"
}

# Main script
case "${1:-}" in
    pause)
        pause_scheduler
        ;;
    resume)
        resume_scheduler
        ;;
    delete)
        delete_scheduler
        ;;
    status)
        show_status
        ;;
    trigger)
        trigger_now
        ;;
    logs)
        show_logs
        ;;
    *)
        show_usage
        exit 1
        ;;
esac

