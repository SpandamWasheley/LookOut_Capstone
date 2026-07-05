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

// Only renders a real ScrollView (with a scroll responder attached) once the
// content is actually measured to be taller than the visible area. Below that
// threshold it's a plain View — so there's no scroll gesture to drag into the
// empty space below short content, instead of fighting RN's scrollEnabled /
// bounces / overScrollMode flags, which don't reliably block that drag here.
export function AutoScrollView({ style, contentContainerStyle, children, ...rest }: AutoScrollViewProps) {
  const [containerHeight, setContainerHeight] = useState(0);
  const [contentHeight, setContentHeight] = useState(0);
  const scrollEnabled = contentHeight > containerHeight && containerHeight > 0;

  const onOuterLayout = (e: LayoutChangeEvent) => {
    setContainerHeight(e.nativeEvent.layout.height);
  };
  const onContentLayout = (e: LayoutChangeEvent) => {
    setContentHeight(e.nativeEvent.layout.height);
  };

  return (
    <View style={[{ flex: 1 }, style]} onLayout={onOuterLayout}>
      {scrollEnabled ? (
        <ScrollView
          style={{ flex: 1 }}
          contentContainerStyle={contentContainerStyle}
          showsVerticalScrollIndicator={false}
          {...rest}
        >
          <View onLayout={onContentLayout}>{children}</View>
        </ScrollView>
      ) : (
        <View style={contentContainerStyle} onLayout={onContentLayout}>
          {children}
        </View>
      )}
    </View>
  );
}
