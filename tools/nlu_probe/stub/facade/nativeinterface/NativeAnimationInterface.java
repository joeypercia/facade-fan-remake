package facade.nativeinterface;

// Stand-in for the game's native interface while NluProbe runs the parser outside the game: the
// parser only logs through it (a tick count), and the real class loads the 32-bit engine DLL.
public class NativeAnimationInterface {
    public static int getTickCount() {
        return (int) System.currentTimeMillis();
    }
}
