<script>
	import {
		Activity,
		Bell,
		Boxes,
		Car,
		Check,
		ChevronLeft,
		ChevronRight,
		CircleAlert,
		Clock,
		Folder,
		Inbox,
		LayoutDashboard,
		Lock,
		LogIn,
		LogOut,
		Mail,
		Megaphone,
		MessageCircle,
		MoreHorizontal,
		Pause,
		Play,
		Plus,
		RefreshCw,
		ScrollText,
		Search,
		Send,
		Server,
		Settings,
		Ticket,
		Trash2,
		TriangleAlert,
		Users,
		X
	} from 'lucide-svelte';

	// Recibe el nombre corto del contrato (`logs`, `chat`) o el PascalCase de
	// lucide (`ScrollText`). La switchera es explícita: nada de imports
	// dinámicos, que el bundler no puede sacudir.
	let { name = 'dashboard', size = 20, stroke = 1.75, title = '' } = $props();

	const Componente = $derived.by(() => {
		switch (String(name).replace(/[-_\s]/g, '').toLowerCase()) {
			case 'dashboard':
			case 'layoutdashboard':
			case 'estado':
				return LayoutDashboard;
			case 'logs':
			case 'scrolltext':
				return ScrollText;
			case 'settings':
			case 'configuracion':
			case 'configuración':
				return Settings;
			case 'apps':
			case 'boxes':
				return Boxes;
			case 'bell':
			case 'avisos':
			case 'notificaciones':
				return Bell;
			case 'megaphone':
				return Megaphone;
			case 'clock':
			case 'asistencia':
				return Clock;
			case 'whatsapp':
			case 'chat':
			case 'messagecircle':
				return MessageCircle;
			case 'mail':
			case 'correo':
				return Mail;
			case 'ticket':
			case 'triaje':
				return Ticket;
			case 'users':
				return Users;
			case 'check':
				return Check;
			case 'alert':
			case 'trianglealert':
			case 'circlealert':
				return TriangleAlert;
			case 'refresh':
			case 'refreshcw':
				return RefreshCw;
			case 'search':
				return Search;
			case 'chevron':
			case 'chevronright':
				return ChevronRight;
			case 'chevronleft':
				return ChevronLeft;
			case 'plus':
				return Plus;
			case 'logout':
				return LogOut;
			case 'login':
				return LogIn;
			case 'send':
				return Send;
			case 'trash':
			case 'trash2':
				return Trash2;
			case 'play':
				return Play;
			case 'pause':
				return Pause;
			case 'folder':
				return Folder;
			case 'car':
				return Car;
			case 'inbox':
				return Inbox;
			case 'server':
				return Server;
			case 'activity':
				return Activity;
			case 'lock':
				return Lock;
			case 'more':
			case 'morehorizontal':
				return MoreHorizontal;
			case 'x':
			case 'close':
				return X;
			default:
				return CircleAlert;
		}
	});
</script>

<Componente
	size={size}
	strokeWidth={stroke}
	aria-hidden={title ? undefined : 'true'}
	role={title ? 'img' : undefined}
	aria-label={title || undefined}
/>
