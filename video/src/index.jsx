import { registerRoot, Composition } from 'remotion';
import { Video, TOTAL } from './Video';

const Root = () => (
  <Composition id="Hivemind" component={Video} durationInFrames={TOTAL} fps={30} width={1920} height={1080} />
);
registerRoot(Root);
