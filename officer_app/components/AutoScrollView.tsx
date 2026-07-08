import React, { useState } from "react";
import {
  LayoutChangeEvent,
  ScrollView,
  ScrollViewProps,
  StyleProp,
  ViewStyle,
} from "react-native";

interface AutoScrollViewProps extends ScrollViewProps {
  style?: StyleProp<ViewStyle>;
  contentContainerStyle?: StyleProp<ViewStyle>;
  children: React.ReactNode;
}

// Keeps a single ScrollView mounted at all times and only toggles its
// `scrollEnabled` prop once the content is measured to be taller than the
// visible area. Swapping between a View and a ScrollView (the previous
// approach) remounted the whole child subtree and, because the two branches
// measured content height differently, could oscillate around the threshold —
// which showed up as a flicker when opening a screen. `scrollEnabled={false}`
// already fully blocks the scroll gesture on short content, so there's no need
// to swap the component to stop the drag into empty space below.
export function AutoScrollView({ style, contentContainerStyle, children, ...rest }: AutoScrollViewProps) {
  const [containerHeight, setContainerHeight] = useState(0);
  const [contentHeight, setContentHeight] = useState(0);
  // +1px epsilon so content that fits exactly doesn't jitter the flag.
  const scrollEnabled = containerHeight > 0 && contentHeight > containerHeight + 1;

  const onLayout = (e: LayoutChangeEvent) => {
    setContainerHeight(e.nativeEvent.layout.height);
  };

  return (
    <ScrollView
      showsVerticalScrollIndicator={false}
      {...rest}
      style={[{ flex: 1 }, style]}
      contentContainerStyle={contentContainerStyle}
      scrollEnabled={scrollEnabled}
      onLayout={onLayout}
      onContentSizeChange={(_w, h) => setContentHeight(h)}
    >
      {children}
    </ScrollView>
  );
}
