import { test, expect } from '@playwright/test';

test.describe('CSIS SmartAssist Critical User Journeys', () => {

  test('Unauthenticated Redirect Flow', async ({ page }) => {
    // Attempting to visit /chat directly without being logged in should redirect to /
    await page.goto('/chat');
    
    // Playwright automatically waits for URL to change
    await expect(page).toHaveURL('/');
    
    // Check that login screen is visible
    await expect(page.getByRole('button', { name: 'ENTER AS LOCAL ADMIN' })).toBeVisible();
  });

  // Since actual authentication uses Google OAuth and Supabase with strict domain checking,
  // we would typically use a seeded user session cookie for E2E tests.
  // For the purpose of these robust tests, we'll demonstrate the E2E structure assuming 
  // auth state can be mocked or injected via Playwright storageState, or we'll mock the Supabase auth.
  
  // NOTE: In a true CI environment, we use `storageState` with a valid test user token.
  test.describe('Authenticated flows', () => {
    
    // Intercept API calls to mock the backend responses, allowing full frontend E2E testing
    // without relying on Gemini limits or live DB state during CI.
    test.beforeEach(async ({ page }) => {
      // Mock user session on frontend via localStorage/cookies or intercept auth endpoints if needed
      await page.addInitScript(() => {
        window.localStorage.setItem("smartassist_local_auth", "true");
      });
      
      // Mock the initial session load
      await page.route('**/api/chat/sessions', async route => {
        if (route.request().method() === 'GET') {
          await route.fulfill({ json: [] });
        } else if (route.request().method() === 'POST') {
          await route.fulfill({ json: { id: 'sess-123', title: 'New Chat' } });
        }
      });
      await page.route('**/api/chat/sessions/sess-123/messages', async route => {
        await route.fulfill({ json: [] });
      });
      
      // Mock bookings list
      await page.route('**/api/calendar/bookings*', async route => {
        await route.fulfill({ json: [] });
      });
    });

    test('Chat & RAG Journey', async ({ page }) => {
      // Go directly to chat, intercepting auth to simulate logged in state
      await page.goto('/chat');
      
      // Verify empty state
      await expect(page.getByRole('heading', { name: 'How can I help you?' })).toBeVisible();

      // Mock chat send stream response
      await page.route('**/api/chat/send', async route => {
        const payload = route.request().postDataJSON();
        expect(payload.message).toBe('What are the rules for TA allocation?');
        
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'data: {"type":"chunk","text":"Here are the rules for TA allocation..."}\n\ndata: {"type":"done"}\n\n',
        });
      });

      // Send a message through the deep-link flow used after creating a chat.
      await page.goto('/chat/sess-123?q=What%20are%20the%20rules%20for%20TA%20allocation%3F');

      // Verify the response appears
      await expect(page.locator('text=Here are the rules for TA allocation...')).toBeVisible();
    });

    test('Booking Journey', async ({ page }) => {
      // Mock auth
      await page.goto('/chat');

      // Mock chat returning a booking proposal
      await page.route('**/api/chat/send', async route => {
        await route.fulfill({
          status: 200,
          contentType: 'text/event-stream',
          body: 'data: {"type":"metadata","intent":"booking","message_id":"msg-123","interactive_type":"booking_proposal","interactive_payload":{"room_id":"dlt_8","room_name":"DLT-8","title":"Test Event","start_time":"2026-07-13T11:00:00+05:30","end_time":"2026-07-13T12:00:00+05:30","description":""}}\n\ndata: {"type":"chunk","text":"I can book that for you."}\n\ndata: {"type":"done"}\n\n',
        });
      });

      // Send booking request through the deep-link flow used after creating a chat.
      await page.goto('/chat/sess-123?q=Book%20DLT-8%20tomorrow%2011am%20to%2012pm');

      // Wait for the Booking Proposal card to appear
      await expect(page.getByText('DRAFT PROPOSAL')).toBeVisible();
      await expect(page.getByText('DLT-8', { exact: true })).toBeVisible();
      await expect(page.getByText('Test Event', { exact: true })).toBeVisible();

      // Mock the create booking API
      await page.route('**/api/bookings', async route => {
        await route.fulfill({
          status: 200,
          json: {
            id: 'b-999',
            user_id: 'local-user',
            room_id: 'dlt_8',
            room_name: 'DLT-8',
            title: 'Test Event',
            description: '',
            start_time: '2026-07-13T11:00:00+05:30',
            end_time: '2026-07-13T12:00:00+05:30',
            status: 'pending',
            acknowledged_use: true,
            acknowledged_availability: true,
            created_at: '2026-07-01T00:00:00Z',
            updated_at: '2026-07-01T00:00:00Z',
          }
        });
      });

      await page.getByLabel('I confirm this request is for permitted academic or study use.').check();
      await page.getByLabel('I understand the calendar shows recorded bookings only; actual room availability is confirmed during approval.').check();
      await page.getByRole('button', { name: 'Submit Request' }).click();

      // Verify UI changes to submitted state
      await expect(page.getByText('PENDING APPROVAL')).toBeVisible();
    });
  });
});
