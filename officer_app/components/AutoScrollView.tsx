import React, { useState } from "react";
import {
  LayoutChangeEvent,
  ScrollView,
  ScrollViewProps,
  StyleProp,
  View,
  ViewStyle,
} from "react-native";

interface AutoScrollViewProps extends ScrollViewProps {
  style?: StyleProp<ViewStyle>;
  contentContainerStyle?: StyleProp<ViewStyle>;
  children: React.ReactNode;
}

// Always renders a ScrollView, but only enables scrolling once the content is
// actually taller than the visible area — so there's no scroll gesture to drag
// into the empty space below short content.
//
// This intentionally does NOT swap between a View and a ScrollView based on the
// measurement: doing so measured two differently-styled nodes (padded vs. not),
// so when the content height sat near the viewport height the enabled/disabled
// decision oscillated forever and flickered the whole screen. Toggling
// `scrollEnabled` on a single, stable ScrollView avoids that entirely.
export function AutoScrollView({ style, contentContainerStyle, children, ...rest }: AutoScrollViewProps) {
  const [containerHeight, setContainerHeight] = useState(0);
  const [contentHeight, setContentHeight] = useState(0);
  // +1 tolerance so sub-pixel rounding on content that exactly fills the
  // viewport can't flip scrolling on and off.
  const scrollEnabled = containerHeight > 0 && contentHeight > containerHeight + 1;

  const onOuterLayout = (e: LayoutChangeEvent) => {
    setContainerHeight(e.nativeEvent.layout.height);
  };

  return (
    <ScrollView
      style={[{ flex: 1 }, style]}
      contentContainerStyle={contentContainerStyle}
      showsVerticalScrollIndicator={false}
      onLayout={onOuterLayout}
      onContentSizeChange={(_w, h) => setContentHeight(h)}
      {...rest}
      scrollEnabled={scrollEnabled}
    >
      {children}
    </ScrollView>
  );
}
