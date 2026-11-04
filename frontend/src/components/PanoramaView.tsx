import { WebView } from "react-native-webview";

type Props = {
  uri: string;
};

export default function PanoramaView({ uri }: Props) {
  return (
    <WebView
      source={{ uri }}
      style={{ flex: 1 }}
      javaScriptEnabled
      domStorageEnabled
      startInLoadingState
    />
  );
}