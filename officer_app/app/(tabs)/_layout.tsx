import { Tabs } from "expo-router";
import React from "react";

import { TabBar } from "@/components/TabBar";

/**
 * The tab navigator is kept, but it draws none of its own chrome: `tabBar`
 * hands rendering to components/TabBar.tsx, and the per-screen options are
 * gone because the labels and icons live there with it. Everything this file
 * used to configure — tint colours, the border, the web-only height fudge —
 * was describing the default bar, which is no longer on screen.
 */
export default function TabLayout() {
  return (
    <Tabs screenOptions={{ headerShown: false }} tabBar={(props) => <TabBar {...props} />}>
      <Tabs.Screen name="index" />
      <Tabs.Screen name="history" />
      <Tabs.Screen name="profile" />
    </Tabs>
  );
}
