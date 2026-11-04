type Props = {
  uri: string;
};

export default function PanoramaView({ uri }: Props) {
  return (
    <iframe
      src={uri}
      title="LEIA sample 360-degree panorama"
      allowFullScreen
      style={{
        width: "100%",
        height: "100%",
        border: 0,
        flex: 1,
      }}
    />
  );
}