/**
 * Supabase middleware helper — refreshes auth session on every request.
 *
 * Used by the Next.js middleware to keep the session alive and
 * handle cookie synchronization between browser and server.
 *
 * In LOCAL_AUTH mode, all routes are accessible without authentication.
 */

import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function updateSession(request: NextRequest) {
  // ── Local Auth Bypass ─────────────────────────────────────────────────
  // When LOCAL_AUTH is enabled (e.g. intranet deployment without Google OAuth),
  // skip all Supabase session checks and allow every route through.
  if (process.env.LOCAL_AUTH === "true") {
    return NextResponse.next({ request });
  }

  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL || "https://placeholder.supabase.co",
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "placeholder",
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet: any[]) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value)
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options)
          );
        },
      },
    }
  );

  // Refresh session — this is critical for keeping the session alive
  const {
    data: { user },
  } = await supabase.auth.getUser();

  // Define protected and public routes
  const pathname = request.nextUrl.pathname;
  const isPublicRoute = 
    pathname === "/" || 
    pathname.startsWith("/auth-callback");
  const isProtectedRoute = !isPublicRoute;

  // Redirect unauthenticated users to landing page
  if (!user && isProtectedRoute) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    return NextResponse.redirect(url);
  }

  return supabaseResponse;
}
