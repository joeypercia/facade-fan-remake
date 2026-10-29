// Runs Façade's own 2005 parser (TemplateCompiler, Jess rules) on lines of text, outside the game, and
// prints the discourse acts it picks for each. Same setup as TemplateCompiler.ConsoleMain, minus its
// Swing memory debugger. Reads the rules from the player's install at run time; nothing is copied.
//
//   javac -cp <Facade>/util/classes -d out NluProbe.java
//   java -cp out;<Facade>/util/classes NluProbe <Facade>/util/sources/facade lines.txt [beat]
//
// Output: one line per input, "text\tDA1 param; DA2 param; ...".

import java.io.*;
import java.util.*;
import java.util.zip.InflaterInputStream;

public class NluProbe {
    public static void main(String[] args) throws Exception {
        try {
            run(args);
        } catch (Throwable t) {
            t.printStackTrace();
            System.exit(1);   // the parser's timer threads would keep the JVM alive
        }
    }

    // Run with the working directory set to <dir>: Jess loads scriptlib.clp from there.
    static void run(String[] args) throws Exception {
        String dir = args[0];
        Hashtable templates = (Hashtable) load(dir + "\\general.rul");
        Hashtable beats = (Hashtable) load(dir + "\\general.map");
        String beat = args.length > 2 ? args[2] : null;
        if (beat == null || !beats.containsKey(beat)) {
            System.err.println("beats: " + beats.keySet());
            if (beat != null) System.exit(2);
            beat = (String) beats.keySet().iterator().next();
        }

        TemplateCompiler.RefCountRete rete = new TemplateCompiler.RefCountRete();
        rete.addJessListener(new TemplateCompiler.DebugListener(false));
        rete.setEventMask(rete.getEventMask() | 16 | 2 | 4096);
        wm.WorkingMemory memory = new wm.WorkingMemory("TripMemory");
        rete.addJessListener(new TemplateCompiler.FactListener(new TemplateCompiler.TimeOut(rete)));

        PrintStream out = System.out;
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        System.setOut(new PrintStream(buf, true, "UTF-8"));
        TemplateCompiler.Preprocessor.initializeReteEngine(rete);
        for (Iterator i = ((HashSet) beats.get(beat)).iterator(); i.hasNext(); )
            rete.executeCommands((String) templates.get(i.next()));
        for (Iterator i = ((HashSet) templates.get("_CONST")).iterator(); i.hasNext(); )
            rete.executeCommand((String) i.next());
        facade.characters.wmedef.AnaphoricReferenceWME ana = new facade.characters.wmedef.AnaphoricReferenceWME();
        facade.characters.wmedef.PostBeatMixinInfoWME mix = new facade.characters.wmedef.PostBeatMixinInfoWME();
        memory.addWME(ana);
        memory.addWME(mix);
        rete.store("__factKey", "anaphoricRef");
        rete.store("__wmeKey", ana);
        rete.executeCommand("(shadowWME __factKey __wmeKey)");
        rete.store("__factKey", "postBeatMixinInfo");
        rete.store("__wmeKey", mix);
        rete.executeCommand("(shadowWME __factKey __wmeKey)");

        BufferedReader in = new BufferedReader(new InputStreamReader(new FileInputStream(args[1]), "UTF-8"));
        for (String line; (line = in.readLine()) != null; ) {
            line = line.trim();
            if (line.length() == 0 || line.startsWith("#")) continue;
            buf.reset();
            try {
                TemplateCompiler.Preprocessor.Process(line.toLowerCase(), rete);
                rete.run();
                rete.reset();
            } catch (jess.JessException e) {
                out.println(line + "\tERROR " + e.getMessage());
                continue;
            }
            StringBuilder acts = new StringBuilder();
            for (String l : buf.toString("UTF-8").split("\n")) {
                int k = l.indexOf("==> (DA eDAType_");
                if (k < 0) continue;
                String[] f = l.substring(k + 16, l.lastIndexOf(')')).split(" ");
                if (f[0].equals("SystemDoesntUnderstand")) continue;
                if (acts.length() > 0) acts.append("; ");
                acts.append(f[0]);
                for (int p = 1; p < f.length; p++)
                    if (!f[p].endsWith("NOPARAM") && !f[p].endsWith("NOTASPRITE"))
                        acts.append(' ').append(f[p].replaceFirst("^e[A-Za-z]*_", ""));
            }
            out.println(line + "\t" + (acts.length() > 0 ? acts : "-"));
        }
        System.exit(0);
    }

    static Object load(String path) throws Exception {
        try (ObjectInputStream o = new ObjectInputStream(new InflaterInputStream(new FileInputStream(path)))) {
            return o.readObject();
        }
    }
}
