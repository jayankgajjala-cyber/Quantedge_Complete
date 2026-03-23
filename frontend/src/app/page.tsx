import { redirect } from "next/navigation";

// Root "/" redirects to "/portfolio" which is the default dashboard view.
// The Sidebar links to "/portfolio" (not "/") so active state highlights correctly.
export default function RootPage() {
  redirect("/portfolio");
}
